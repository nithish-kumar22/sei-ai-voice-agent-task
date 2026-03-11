"""
Wise "Where is my money?" FAQ → Qdrant pipeline.
Scrapes only that section, parses articles (paragraphs, bullets, tables),
chunks, embeds with sentence-transformers (512-dim), and upserts to Qdrant.
"""
import os
import re
import time
import uuid
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, PayloadSchemaType

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOPIC_URL = "https://wise.com/help/topics/5bVKT0uQdBrDp6T62keyfz/sending-money"
SECTION_TITLE = "Where is my money?"
WISE_BASE = "https://wise.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0"
REQUIRED_VECTOR_SIZE = 512
BATCH_SIZE = 32


def load_config():
    """Load and validate config from .env."""
    load_dotenv()
    config = {
        "QDRANT_URL": os.getenv("QDRANT_URL"),
        "QDRANT_API_KEY": os.getenv("QDRANT_API_KEY"),
        "QDRANT_COLLECTION_NAME": os.getenv("QDRANT_COLLECTION_NAME"),
        "EMBEDDING_MODEL": os.getenv("EMBEDDING_MODEL"),
    }
    missing = [k for k, v in config.items() if k != "QDRANT_API_KEY" and not (v and v.strip())]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}. Check .env.")
    if config["QDRANT_API_KEY"]:
        config["QDRANT_API_KEY"] = config["QDRANT_API_KEY"].strip()
    return config


# ---------------------------------------------------------------------------
# Topic page: get "Where is my money?" article URLs
# ---------------------------------------------------------------------------

def get_where_is_my_money_article_urls(session: httpx.Client, topic_url: str) -> list[str]:
    """Fetch topic page, find section 'Where is my money?', return only those article URLs."""
    # resp = session.get(topic_url)
    # resp.raise_for_status()
    # soup = BeautifulSoup(resp.text, "html.parser")

    target_links = [
        "https://wise.com/help/articles/2452305/how-do-i-check-my-transfers-status?origin=topic-5bVKT0uQdBrDp6T62keyfz",
        "https://wise.com/help/articles/2941900/when-will-my-money-arrive?origin=topic-5bVKT0uQdBrDp6T62keyfz",
        "https://wise.com/help/articles/2977950/why-does-it-say-my-transfers-complete-when-the-money-hasnt-arrived-yet?origin=topic-5bVKT0uQdBrDp6T62keyfz",
        "https://wise.com/help/articles/2977951/why-is-my-transfer-taking-longer-than-the-estimate?origin=topic-5bVKT0uQdBrDp6T62keyfz",
        "https://wise.com/help/articles/2932689/what-is-a-proof-of-payment?origin=topic-5bVKT0uQdBrDp6T62keyfz",
        "https://wise.com/help/articles/2977938/whats-a-banking-partner-reference-number?origin=topic-5bVKT0uQdBrDp6T62keyfz"
        ]
    # Find the h2 (or h3) that exactly matches the section title
    # for tag in soup.find_all(["h2", "h3"]):
    #     if (tag.get_text() or "").strip() != SECTION_TITLE:
    #         continue
    #     print("tag", tag)
    #     # Collect links from following siblings until we hit the next section heading
    #     for sib in tag.find_next_siblings():
    #         if sib.name in ("h2", "h3"):
    #             break
    #         print("sibling", sib)
    #         for a in sib.find_all("a", href=True):
    #             href = (a.get("href") or "").strip()
    #             print("href", href)
    #             if "/help/articles/" in href:
    #                 full_url = urljoin(WISE_BASE, href)
    #                 if full_url not in target_links:
    #                     target_links.append(full_url)
    #     break
    return target_links


# ---------------------------------------------------------------------------
# Article fetch
# ---------------------------------------------------------------------------

def fetch_article(session: httpx.Client, url: str) -> dict:
    """Fetch one article; return title, url, section, content_html."""
    resp = session.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title = ""
    t = soup.find("h1") or soup.find("title")
    if t:
        title = (t.get_text() or "").strip()

    # Main content: common patterns for help articles
    body = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"article|content|body|post", re.I))
        or soup.find("div", {"role": "main"})
    )
    if not body:
        body = soup.find("body") or soup

    # Remove noisy blocks
    for block in body.find_all(["script", "nav", "footer", "aside", "form", "button"]):
        block.decompose()
    for block in body.find_all(string=re.compile(r"Was this article helpful|Related articles", re.I)):
        parent = block.parent
        if parent:
            parent.decompose()
    for block in body.find_all(["div", "section"], class_=re.compile(r"related|feedback|helpful|sidebar", re.I)):
        block.decompose()

    content_html = str(body)
    return {
        "title": title or url,
        "url": url,
        "section": SECTION_TITLE,
        "content_html": content_html,
    }


# ---------------------------------------------------------------------------
# Parse and normalize HTML → structured blocks (paragraph, list, table, heading)
# ---------------------------------------------------------------------------

def _text(elem) -> str:
    return (elem.get_text(separator=" ", strip=True) or "").strip()


def _table_to_markdown(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [_text(td) for td in tr.find_all(["th", "td"])]
        if cells:
            rows.append(" | ".join(cells))
    if not rows:
        return ""
    return "\n".join(rows)


def parse_and_normalize(html: str, title: str) -> list[dict]:
    """
    Parse article HTML into a list of blocks: { "type": "paragraph"|"list"|"table"|"heading", "text": "..." }.
    Preserves structure for chunking (tables and lists as separate blocks).
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks = []

    # Add title as first heading
    if title:
        blocks.append({"type": "heading", "text": title})

    # Iterate in document order over body/main/article content
    for tag in soup.find_all(["p", "ul", "ol", "table", "h2", "h3", "h4"]):
        if tag.name == "p":
            t = _text(tag)
            if t:
                blocks.append({"type": "paragraph", "text": t})
        elif tag.name in ("h2", "h3", "h4"):
            t = _text(tag)
            if t:
                blocks.append({"type": "heading", "text": t})
        elif tag.name in ("ul", "ol"):
            items = [_text(li) for li in tag.find_all("li", recursive=False) if _text(li)]
            if items:
                if tag.name == "ul":
                    lines = [f"- {s}" for s in items]
                else:
                    lines = [f"{i + 1}. {s}" for i, s in enumerate(items)]
                blocks.append({"type": "list", "text": "\n".join(lines)})
        elif tag.name == "table":
            md = _table_to_markdown(tag)
            if md:
                blocks.append({"type": "table", "text": md})

    return blocks


# ---------------------------------------------------------------------------
# Chunking: group blocks into ~200–500 token (char-based) chunks, tables as own chunk
# ---------------------------------------------------------------------------

def chunk_content(blocks: list[dict], article_meta: dict) -> list[dict]:
    """
    Split blocks into chunks. Each chunk has "text" and "metadata" (source, topic, article_title, url, content_type).
    Tables and lists are kept as single chunks when possible; paragraphs grouped.
    """
    chunks = []
    # ~4 chars per token approx → 800–2000 chars
    min_chunk_chars = 200
    max_chunk_chars = 2000
    current_text = []
    current_types = set()
    current_len = 0

    def flush():
        nonlocal current_text, current_types, current_len
        if not current_text:
            return
        content_type = "mixed" if len(current_types) > 1 else (list(current_types)[0] if current_types else "paragraph")
        chunks.append({
            "text": "\n\n".join(current_text),
            "metadata": {
                "source": "wise_help",
                "topic": article_meta.get("section", SECTION_TITLE),
                "article_title": article_meta.get("title", ""),
                "url": article_meta.get("url", ""),
                "content_type": content_type,
            },
        })
        current_text = []
        current_types = set()
        current_len = 0

    for blk in blocks:
        btype = blk["type"]
        text = blk["text"]
        if not text:
            continue
        blen = len(text)

        if btype == "table":
            flush()
            chunks.append({
                "text": text,
                "metadata": {
                    "source": "wise_help",
                    "topic": article_meta.get("section", SECTION_TITLE),
                    "article_title": article_meta.get("title", ""),
                    "url": article_meta.get("url", ""),
                    "content_type": "table",
                },
            })
            continue

        if btype == "list":
            flush()
            chunks.append({
                "text": text,
                "metadata": {
                    "source": "wise_help",
                    "topic": article_meta.get("section", SECTION_TITLE),
                    "article_title": article_meta.get("title", ""),
                    "url": article_meta.get("url", ""),
                    "content_type": "list",
                },
            })
            continue

        if btype == "heading":
            if current_len > 0 and current_len + blen > max_chunk_chars:
                flush()
            current_text.append(text)
            current_types.add("heading")
            current_len += blen
            continue

        # paragraph
        if current_len + blen > max_chunk_chars and current_len >= min_chunk_chars:
            flush()
        current_text.append(text)
        current_types.add("paragraph")
        current_len += blen

    flush()
    return chunks


# ---------------------------------------------------------------------------
# Embeddings (sentence-transformers, must be 512-dim)
# ---------------------------------------------------------------------------

def embed_chunks(chunks: list[dict], config: dict) -> list[list[float]]:
    """Load sentence-transformers model, encode chunk texts; return list of 512-dim vectors."""
    from sentence_transformers import SentenceTransformer  # lazy import to avoid numpy/sklearn ABI issues at startup
    model_name = config["EMBEDDING_MODEL"]
    model = SentenceTransformer(model_name)
    dim = model.get_sentence_embedding_dimension()
    if dim != REQUIRED_VECTOR_SIZE:
        raise SystemExit(
            f"Embedding model dimension is {dim}; Qdrant collection expects {REQUIRED_VECTOR_SIZE}. "
            f"Use a model that outputs {REQUIRED_VECTOR_SIZE} dimensions."
        )
    texts = [c["text"] for c in chunks]
    vectors = model.encode(texts, show_progress_bar=len(texts) > 5)
    return vectors.tolist()


# ---------------------------------------------------------------------------
# Qdrant upsert (use existing collection 512, Cosine)
# ---------------------------------------------------------------------------

def upsert_to_qdrant(points: list[dict], config: dict) -> None:
    """Init Qdrant client, ensure collection exists (512, Cosine), upsert points in batches."""
    kwargs = {"url": config["QDRANT_URL"]}
    if config.get("QDRANT_API_KEY"):
        kwargs["api_key"] = config["QDRANT_API_KEY"]
    client = QdrantClient(**kwargs)
    coll = config["QDRANT_COLLECTION_NAME"]

    collections = client.get_collections().collections
    if not any(c.name == coll for c in collections):
        client.create_collection(
            collection_name=coll,
            vectors_config=VectorParams(size=REQUIRED_VECTOR_SIZE, distance=Distance.COSINE),
        )
    try:
        client.create_payload_index(
            collection_name=coll,
            field_name="topic",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass  # Index already exists

    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        qdrant_points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=p["vector"],
                payload={
                    "text": p["text"],
                    **p["metadata"],
                },
            )
            for p in batch
        ]
        client.upsert(collection_name=coll, points=qdrant_points)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    print("Config loaded. Fetching 'Where is my money?' article URLs...")
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as session:
        urls = get_where_is_my_money_article_urls(session, TOPIC_URL)
    
    print(f"Found {len(urls)} articles.")

    articles = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as session:
        for url in urls:
            try:
                art = fetch_article(session, url)
                articles.append(art)
                time.sleep(0.5)
            except Exception as e:
                print(f"Skip {url}: {e}")

    print(f"Fetched {len(articles)} articles.")

    all_chunks = []
    for art in articles:
        blocks = parse_and_normalize(art["content_html"], art["title"])
        chunks = chunk_content(blocks, art)
        for c in chunks:
            all_chunks.append(c)
    print(f"Created {len(all_chunks)} chunks.")

    print("Generating embeddings (512-dim)...")
    vectors = embed_chunks(all_chunks, config)

    points = [
        {"text": c["text"], "vector": v, "metadata": c["metadata"]}
        for c, v in zip(all_chunks, vectors)
    ]
    upsert_to_qdrant(points, config)
    print("Upserted to Qdrant.")


if __name__ == "__main__":
    main()
