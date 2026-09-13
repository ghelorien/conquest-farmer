# Discord notification policy

The current policy sends a status summary every 15 minutes, restock departure
and completion notifications, and persistent-failure alerts. This supersedes
the earlier stop-only preference. Per-action farming, deaths, revives, reconnect
attempts and focus recovery remain silent unless a persistent failure
qualifies for an alert below. The terminal_v3 state key preserves existing
failure timers and delivered-alert history; monitor revision is hourly_inventory_pickups_v4.

The first status summary is sent when this update loads, then every 900 seconds.
The next due time persists across notifier restarts. Summaries show available
activity, level, fresh HP, verified kills in the preceding 900 seconds and supply counts;
stale readings are omitted or labeled. Offline summaries coalesce to the latest.
The kill total reads verified increments from the read-only trial database across
session rollovers. Missing or unreadable history is unavailable, never zero.
Summaries also show verified kills in the last 60 seconds against the user's
30-kills-per-minute target; this is a measured count, not attack attempts.
Restocking start/completion are consumed from the durable route event log, with
a persistent byte cursor and partial-line handling. Existing historical trips
are not replayed. Critical failures take priority over queued routine messages.

A terminal stop must remain unresolved for 60 seconds before one "Needs
attention" alert is queued. A living route with a missing heartbeat is treated
as unresponsive after three minutes, then uses the same 60-second confirmation.
These intervals detect a persistent failure; they do not themselves retry or
repair farming. Working recovery/travel cancels pending failure alerts.

Blocked navigation also alerts after 60 seconds without observed movement,
additional kills or a verified pickup, even while the controller keeps sending
fresh heartbeats and reports Farming On. Short detours stay silent. The timer
persists across notifier restarts, and counter resets do not count as progress.
One alert remains open until recovery; a cleared navigation flag or running
process alone cannot confirm recovery from a navigation stall. Actual progress
cancels an undelivered alert. Delivered alerts get a recovery message after
progress plus two seconds of active farming with navigation unblocked.

If recovery occurs before an alert can be delivered, discard that alert and
stay silent. If Discord received the alert, send one "Farming resumed" message
after two seconds of confirmed active, enabled, focused farming with a living
player. A restock or reconnect attempt alone is not farming recovery. Pending
failure timers and delivered-alert state persist across notifier restarts.
Routine message and old alert backlogs are cleared when this policy loads.

The webhook is Windows DPAPI encrypted in .runtime/discord-webhook.dpapi,
excluded from exports, and never logged. Delivery requires a Discord message ID,
disables mentions, rejects redirects, honors rate limits and durably queues
failed sends. Ambiguous network errors can duplicate a message on retry.
The PC and notifier must be running to send alerts.

Start scripts/run_discord_notifications.py; the desktop app also starts it.
A Windows file lock prevents duplicate monitors. State is in
.runtime/discord-notifications.json and health in reports/discord-status.json.
.runtime/discord.paused pauses messages without controlling farming.

Tests cover short-stop silence, persistent failures, deduplication, notifier
restart persistence, offline queue recovery, confirmed restart delivery and
silent automatic recovery. Gameplay observations remain memory-only.

Every verified positive pickup now sends its own notification, with the item
name, quality/plus value, quantity and pickup timestamp. Attempts and vanished
ground items without an inventory gain do not generate pickup notifications.
The persistent byte cursor avoids replaying old history after restart.

Inventory gains during farming are now observed independently of tracked ground
clicks. A new valuable inventory UID sends one acquisition event; the first
snapshot establishes a baseline and does not replay existing equipment. Ground
confirmation and inventory detection share UID deduplication. Recovered history
is explicitly labeled with verification time when the pickup time is unknown.
Equipment durability is never treated as pickup quantity.

Quarter-hour summaries also report actual verified kills in the last 3,600
seconds against 1,800/hour, plus the last-15-minute pace multiplied by four.
The projected pace is labeled separately; both windows include all downtime.
Historical windows are retained even when user Stop clears the UI session rate.

Safe app reload preparation has a dedicated reloading phase and persistent UI override: Moving to a safe spot for app reload. Discord announces preparation once and announces resumed farming only after fresh live farming is confirmed. Intermediate healing/Fly events cannot masquerade as town restocking.91 notification/UI/reload/controller tests passed.


Merchant UI: Pause / Resume merchant pauses both activities without disconnecting, then restores their previous permissions. Settings & details retains independent trading/repricing and refill controls. Overview shows notification monitor health for both channels. Saving the encrypted shops webhook starts its alert monitor immediately; shops messages use only that hook.
