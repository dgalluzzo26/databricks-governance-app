# Approver guide

This guide is for category approvers — people (or groups) configured to review access
requests for a sensitivity path or row-filter dimension. You also have everything in the
[User guide](?tab=user).

---

## What you can do

| Page | What it is for |
| --- | --- |
| **Approvals** | Pending requests and renewals you may approve or deny |
| **Attribute explorer** | Who holds a path, and which tables a data-access value unlocks |
| **Access explorer** | Same as any user: inspect a table and request access for yourself |
| **Identity** | Your attributes and your own requests |

Admin configuration (who the approvers are, attribute definitions, policies) stays on
admin-only pages you will not see in the nav.

---

## Approvals inbox

Open **Approvals** from the side nav. The badge shows how many items need a decision.

Each item is an access request (or a renewal) routed to you because:

- you (or a group you belong to) are a **category approver** for that path, or
- you are reviewing as an app admin when no category approver applies

For each item you can:

1. Read the requester, attribute, justification, and requested expiry.
2. **Approve** (optionally adjust expiry) or **Deny**.
3. Add a short note when useful.

Approving assigns the attribute. Denying closes the request without granting access.

---

## Attribute explorer

Use **Attribute explorer** when you need reverse lookup:

- Pick an attribute key and value (or a row-filter dimension for an overview).
- See **who holds** that path.
- For data-access values, see which **reviewed tables** that value unlocks.

Admins see every attribute. As an approver you only see paths you are allowed to decide.

---

## What you do *not* manage

Approvers do not configure:

- Category approver lists or attribute definitions (Identity admin tabs)
- Mask rules, row-filter packs, or policy exceptions
- Grants sync / data-access settings

Ask an app admin for those changes.

---

## Tips for demos and reviews

- Prefer Approvals for day-to-day decisions; use Attribute explorer when someone asks
  “who already has finance.budget?”
- If a request routed to **App admins**, no category approver matched — that is expected
  for some attributes (for example many row-filter dimensions).
- After you approve data-access, the requester may still need a grants sync apply before
  table privileges appear; membership can update sooner.
