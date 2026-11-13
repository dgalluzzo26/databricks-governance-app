# User guide

This guide is for people who use the app to explore tables and request access. You do not
need admin rights to do the things described here.

---

## What you can do

| Page | What it is for |
| --- | --- |
| **Home** | Your identity, groups, and attributes you already hold |
| **Access explorer** | Pick a table, see what is masked or filtered, and request what you need |
| **Identity** | View your attributes, request more access, and track your requests |
| **Help** | This guide |

Admin-only pages (Policies, Data access, Performance, and similar) are hidden from your
navigation.

---

## Access explorer

Access explorer answers: *for this table, what can I see today, and what should I request?*

1. **Browse** the catalog tree on the left, **search** by name, or type a three-part name
   (`catalog.schema.table`) and click **Analyze**.
2. Review the insight:
   - **Columns** — which columns are unmasked, masked, or not governed for you
   - **Row filter** — whether a row filter is attached and whether you can see any rows
   - **Suggested attributes** — the specific values that would widen your access
3. Select the suggestions you need, add a justification and expiry, then submit.

### Suggested attributes

Suggestions are grouped into three kinds:

| Kind | What it unlocks |
| --- | --- |
| **Data access** | Table-level access through managed groups (after grants sync apply is on) |
| **Sensitivity** | Unmasks columns that require that sensitivity path |
| **Row filter** | Unlocks matching rows on a filtered column |

Each row is the **specific** value the table needs — not a broader parent path. Check the
boxes for what you want, then **Request**.

### Row filters and `*`

When a table has a row filter and you do not already hold full access:

- **`*`** (wildcard) is selected by default. Requesting it asks for all rows for that
  dimension.
- To request only some values, search under **Find values**, select the matches, and clear
  `*` for that dimension (selecting a concrete value drops `*` automatically).

You cannot hold both `*` and individual values for the same dimension at once in one
request batch.

### Labels you may see

| Label | Meaning |
| --- | --- |
| **Already held** | You already have this attribute; no need to request it again |
| **Not requestable yet** | The attribute definition is not set up; ask an admin |
| **App admins** / named approvers | Who will review the request if you submit it |

---

## Identity (your attributes)

On **Identity**:

- **My attributes** — values you already hold (and data-access status when relevant)
- **Request access** — ask for an attribute by hand if you know the key and value
- **My requests** — pending, approved, and denied requests you opened

Access explorer is usually faster when you are starting from a table: it pre-fills the
values that table needs.

---

## After you request

1. An **approver** (or an app admin, when no category approver is set) reviews the request.
2. When approved, the attribute is assigned to you.
3. For **data access**, group membership can update right away; table grants may wait until
   the next grants sync in apply mode.
4. Re-open Access explorer and analyze the table again to confirm columns and rows look
   right.
