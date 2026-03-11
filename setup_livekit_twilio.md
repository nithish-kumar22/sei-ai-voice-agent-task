# LiveKit → Twilio → Phone Call Setup Guide

## Architecture

The final working architecture is:

```text
Backend API
     ↓
LiveKit SIP API
     ↓
LiveKit SIP Trunk
     ↓
Twilio Elastic SIP Trunk
     ↓
Twilio PSTN Network
     ↓
User Phone
```

---

# 1. Buy a Twilio Phone Number

Open Twilio Console:

```
Phone Numbers → Manage → Buy a number
```

Purchase a number.

Example:

```
+17624380307
```

This number will be used as the **caller ID**.

---

# 2. Create a Twilio Elastic SIP Trunk

Open:

```
Twilio Console
→ Voice
→ Trunking
→ SIP Trunks
→ Create New
```

Example:

```
Friendly Name: livekit-agent
```

---

# 3. Configure Trunk Termination

Open the trunk:

```
Elastic SIP Trunking
→ livekit-agent
→ Termination
```

Set:

```text
Termination SIP URI:
<your-trunk-name>.pstn.twilio.com
```

Example:

```
seiright.pstn.twilio.com
```

---

# 4. Create Credential List

Go to:

```
Twilio Console
→ Elastic SIP Trunking
→ Credential Lists
```

Create a credential.

Example:

```text
Username: livekit
Password: StrongPassword123!
```

Attach this credential list to the trunk.

```
Trunk → Termination → Authentication
```

Select:

```
Credential List
```

---

# 5. Configure IP Access Control List (ACL)

Twilio must allow requests from LiveKit SIP servers.

Go to:

```
Elastic SIP Trunking
→ IP Access Control Lists
```

Create ACL:

```text
Friendly Name: livekit-network
CIDR Network Address: 143.223.0.0
Range: /16
```

Attach this ACL to the trunk.

```
Termination → Authentication → IP Access Control List
```

---

# 6. Configure Origination URI

Open:

```
Elastic SIP Trunking
→ livekit-agent
→ Origination
```

Add:

```text
sip:<your-livekit-sip-domain>.sip.livekit.cloud
```

Example:

```
sip:51jviuda6dg.sip.livekit.cloud
```

Settings:

```
Priority: 1
Weight: 1
Enabled: ✓
```

---

# 7. Configure LiveKit SIP Trunk

Open LiveKit dashboard:

```
Telephony
→ SIP Trunks
→ Create Outbound Trunk
```

Set:

```text
Provider: Twilio
Host: seiright.pstn.twilio.com
Username: livekit
Password: StrongPassword123!
```

Save the trunk.

---

# 8. Make an Outbound Call from Backend

Example Python code:

```python
from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

livekit_api = api.LiveKitAPI(
    url="LIVEKIT_URL",
    api_key="API_KEY",
    api_secret="API_SECRET",
)

request = CreateSIPParticipantRequest(
    sip_trunk_id="TRUNK_ID",
    sip_number="+17624380307",      # Twilio caller ID
    sip_call_to="+917867972157",    # destination number
    room_name="wise-faq-room",
)

await livekit_api.sip.create_sip_participant(request)
```

---

# 9. Expected SIP Flow

If everything is configured correctly the SIP exchange will be:

```
INVITE
100 Trying
407 Authentication
INVITE (with credentials)
100 Trying
180 Ringing
200 OK
```

The phone should start ringing.

---

# 10. Debugging Checklist

## Check LiveKit Call Logs

```
LiveKit Dashboard
→ Telephony
→ Calls
```

Look for:

```
100 Trying
180 Ringing
200 OK
```

---

## Check Twilio Call Logs

```
Twilio Console
→ Monitor
→ Logs
→ Calls
```

Errors you might see:

| Error | Meaning             |
| ----- | ------------------- |
| 32201 | IP not in ACL       |
| 32202 | Bad SIP credentials |
| 32010 | No origination URI  |

---

# 11. Common Issues

### IP Not in ACL

Twilio error:

```
32201 Authentication failure - source IP Address not in ACL
```

Fix:

Add LiveKit IP range:

```
143.223.0.0/16
```

---

### Wrong SIP Credentials

Twilio error:

```
32202 Authentication failure - bad user credentials
```

Fix:

Ensure LiveKit username/password match Twilio credential list.

---

### Missing Origination URI

Twilio error:

```
32010 No valid active origination URLs found
```

Fix:

Add LiveKit SIP domain as Origination URI.

---

# Final Working Configuration Summary

Twilio:

```
SIP Trunk
   Termination URI: seiright.pstn.twilio.com
   Credential List: livekit
   ACL: 143.223.0.0/16
   Origination URI: sip:51jviuda6dg.sip.livekit.cloud
```

LiveKit:

```
Outbound SIP Trunk
   Host: seiright.pstn.twilio.com
   Username: livekit
   Password: StrongPassword123!
```

Backend:

```
CreateSIPParticipantRequest()
```

---