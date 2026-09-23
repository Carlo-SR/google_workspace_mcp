# Gmail Inbox Triage

How to answer "summarise my inbox", "what came in today", "what still needs a reply" without silently dropping the messages that matter most.

This is a correctness reference, not a convenience one. The failure mode described here produces a confident, well-formatted summary that omits the single most important message in the mailbox, with nothing in the output to indicate anything is missing.

## Contents
- The rule
- The recipe
- Why thread listings cannot be trusted for triage
- Completeness checklist
- Presenting the result

---

## The rule

> **Triage at the message level, never from the first page of a thread listing.**

`search_gmail_messages` queries messages, and the Gmail API returns messages newest first. That is the property triage depends on. Any listing that returns *threads* loses it, because a thread has many dates and the listing has to pick one.

---

## The recipe

### 1. Search messages with an explicit time window

```
search_gmail_messages(
  query = "in:inbox newer_than:2d",
  page_size = 100
)
```

Windows: `newer_than:1d` for today, `newer_than:2d` for a morning overview, `newer_than:7d` for the week, `after:2026/09/15` for a fixed cutoff.

Always bound the window. An unbounded `in:inbox` forces you to paginate the entire mailbox to be sure you have the recent messages.

### 2. Paginate to exhaustion

Follow `next_page_token` until it is absent. There is no shortcut. If a result set reports an estimated count, do not trust it as a stopping condition -- estimates in Gmail-backed APIs are computed over the index and routinely differ from the real count by a factor of two or more.

### 3. Group by `thread_id` yourself

Every result carries both `message_id` and `thread_id`. Collapse the messages into threads in your own code, and sort those threads by their **newest** message. This is the step that repairs the ordering.

### 4. Pull the bodies that matter

For the threads that look consequential, call `get_gmail_threads_content_batch` with their `thread_id`s (auto-batched in chunks of 25).

Do not triage from snippets alone. Snippets truncate at roughly the first line, which is exactly where deadlines, amounts and decisions are *not*. A snippet reading "Dear Sir, thank you for your application" can belong to a message whose third paragraph sets a two-week statutory deadline.

### 5. Decide what is actually open

A thread is not open just because it is unread, and not closed just because it is read.

- If the **last** message in the thread is the user's own (label `SENT`), the thread is handled. Reporting it as pending wastes the user's attention and erodes trust in the whole summary.
- Check `list_gmail_filters` blind spots and drafts: a reply may exist as an unsent draft. Thread reads do not include drafts.
- `UNREAD` correlates with neither importance nor pendency.

---

## Why thread listings cannot be trusted for triage

Thread-oriented search endpoints have to map a multi-date object onto a single sort position. The observed behaviour across Gmail-backed thread APIs is:

> **Threads are ordered by the oldest message in the thread that matches the query, descending.**

With no date filter every message matches, so the effective sort key becomes the thread's **start date**. A thread opened three weeks ago that received a reply this morning is placed three weeks down the list.

Measured against a real mailbox of roughly 200 inbox threads. Position matched the oldest query-matching message in every case, never the newest:

| Thread | Messages in thread | Listed at position |
|---|---|---|
| Contract negotiation | Sep 16, Sep 16, **Sep 21 14:58**, Sep 21 15:33, Sep 23 06:45 | Sep 21 14:58 |
| Supplier escalation | Sep 18, Sep 18, **Sep 22 17:51**, Sep 23 03:56 | Sep 22 17:51 |
| Document comment | Sep 10, **Sep 22 08:29** | Sep 22 08:29 |
| Purchase order | **Sep 22 18:00**, Sep 23 04:00 | Sep 22 18:00 |

Adding a date filter moves the sort key inside the window, which makes the result set complete for that window. It does not make the ordering correct, so step 3 above is still required.

### The bias runs against importance

This is not a cosmetic ordering nuisance. The distortion is systematic and it points the wrong way:

- **Old threads with new replies** are contract negotiations, regulatory correspondence, escalating supplier problems, investor discussions. Long-running threads are long-running precisely because they matter.
- **Brand new threads** are marketing mail, system notifications, cold outreach. These always sort to the top.

A naive listing therefore surfaces the noise and buries the business. In the incident that prompted this document, an inbox summary omitted a counterparty confirming a deal in principle and requesting a cost breakdown, because that thread had been opened 15 days earlier. Everything above it in the listing was newsletters and automated notifications.

---

## Completeness checklist

Before presenting a summary, confirm all of these:

- [ ] Query had an explicit time window
- [ ] Paginated until `next_page_token` was absent
- [ ] Grouped by `thread_id` and re-sorted by newest message
- [ ] Checked whether the last message in each thread is the user's own
- [ ] Checked drafts for threads reported as unanswered
- [ ] Fetched full bodies for anything involving a date, an amount or a decision
- [ ] For any specific person or topic the user named, ran a targeted query (`from:`, `subject:`) instead of filtering the already-fetched set

That last point matters: a targeted query returns a small result set where ordering is irrelevant, so it is the cheapest way to verify that nothing was missed.

---

## Presenting the result

**State the coverage window in the output**, not in a footnote: "Last 48 hours, 79 threads." A summary that looks exhaustive but is not is worse than no summary, because the reader stops looking.

**Group by urgency, not by arrival time.** Deadlines and monetary amounts first, automated notifications last. Arrival order is the one thing the user can already see in their own client.

**Collapse duplicate notifications.** Systems that emit a notification per inbound supplier message double every transaction in the inbox. Report the transaction once.

**Watch shared mailboxes.** Mail to `info@`, `procurement@`, `support@` often reaches the user only as CC, and the sender shown in a listing is then the forwarding address rather than the real correspondent. Open the thread before attributing it.
