# Farmer session statistics

The UI counts verified kill increments from `trial.sqlite3`, using a durable
cursor and total in the local `kill-session.json` checkpoint. Combat runner
restarts, automatic town trips, reconnect delays and safe reloads preserve the
session. The hourly rate includes their elapsed time. Combat Off during a town
trip does not end the session; explicit Farming Off or Stop sets the rate to
zero, and the next start begins a new session.

The reader processes only new journal rows and does not modify the kill log.
Invalid increments, a truncated log or a busy database retain the last verified
total and temporarily withhold the rate. Telemetry failures never stop gameplay.
Both the log and checkpoint stay local and must not be included in a release.

On first deployment during an existing run, preserve that run by seeding the
checkpoint from its recorded start time and a consistent read-only journal
snapshot before the safe app reload. Do not infer a start time from the current
combat runner or discard earlier town-trip downtime.
