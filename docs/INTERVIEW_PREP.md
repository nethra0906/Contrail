# How to explain Contrail in an interview

Answers calibrated to what's actually built, verified during this session. Don't inflate
these — an interviewer who asks one good follow-up question will find the edge of whatever
you overclaim, and "here's exactly what's built and why I stopped there" is a stronger
answer than a vague claim that collapses under one follow-up.

## 30-second explanation

"Contrail is a real-time digital twin of U.S. airspace — it ingests live ADS-B aircraft
data, streams it through a Kafka-based pipeline, and serves it to a live map over a
hand-rolled binary WebSocket protocol for performance. On top of that I've trained and
live-deployed four ML models: a GRU that forecasts an aircraft's position with calibrated
uncertainty, a graph neural network that predicts delay propagation across the airport
network, an autoencoder-based anomaly detector, and a Monte Carlo conflict-probability
estimator. The long-term goal — not yet built — is a counterfactual simulator: fork reality
at a timestamp, inject a disruption, and see how delay propagates through the network."

## 1-minute explanation

Add: "It's built as four independent services — an ingest poller, a stream-processing
assembler, a FastAPI gateway, and a Next.js frontend — connected through Kafka for durable
event replay and Redis for disposable live fanout, with Postgres plus TimescaleDB handling
both the relational data and the time-series position history. I followed a staged build
plan with written ADRs for every real deviation from the original design — for example,
when the ADS-B feed's actual rate limits turned out to be much tighter than assumed, I
documented three rounds of retuning rather than just quietly changing a number. Most
recently I did a full audit-and-harden pass: I found and wired in a fully-built-but-
disconnected anomaly detection module, fixed a schema drift risk that would've had the next
migration silently drop three tables, and — by actually running the full stack end to
end instead of trusting the code at a glance — found a live bug where the map would go
empty for two-thirds of every poll cycle due to a staleness-window mismatch."

## 2-3 minute deep explanation

Walk through the pipeline: "`ingest` polls a community ADS-B aggregator every 90 seconds
for 8 major-hub regions — not the whole country, because I discovered through three rounds
of tuning that the free feed's rate limit couldn't sustain a full-CONUS sweep; that's
documented in an ADR with the actual investigation, not just the final number. Every
position report gets written to Postgres directly and published to Kafka. An `assembler`
service consumes that Kafka topic and runs a small pipeline of pure, unit-tested functions
— no I/O — that classify flight phase, detect takeoff and landing, estimate fuel burn using
a published aircraft-performance model, and check for anomalies like emergency squawks or
unusually steep descents. Only after that pure decision layer runs does the I/O happen:
publish to Redis for the live map, buffer for a Parquet archive in MinIO, write flight
records to Postgres.

"The live map itself uses a binary WebSocket protocol I designed instead of JSON — 21 bytes
per aircraft record, because a human-readable format for thousands of aircraft updating
multiple times a second is a real bandwidth and parse-cost problem in a browser. Subscription
is scoped by H3 — Uber's hexagonal spatial index — so a client only receives updates for
the geographic area it's actually displaying, and the server-side fanout is an O(1) lookup,
not a per-client bounding-box comparison on every update.

"On the ML side, I trained a LightGBM model to predict arrival delay from pre-departure
features — scheduled route, carrier, historical taxi time, destination congestion — using
real BTS government flight data with a strict chronological train/val/test split, because
random splitting would let the model see the future during training in a way that looks
like good accuracy until it meets real traffic. It beats both baselines I built against it
honestly — predicting zero delay, and just propagating the scheduled delay forward — and
there's a promotion gate that only lets a newly trained model go live if it strictly beats
whatever's currently serving.

"On top of that I built the other four models the spec calls Network Intelligence: a GRU
with quantile heads for trajectory forecasting, trained on real historical ADS-B position
data and exported to ONNX so live serving doesn't need to import PyTorch at all; a diffusion
graph convolution plus temporal GRU for delay propagation, which is the model that actually
powers the flagship feature — it beats both required baselines, including a LightGBM model
that gets one hop of neighbor-airport delay as a feature, so the multi-hop graph propagation
is demonstrably adding value, not just 'using a bigger model'; a convolutional autoencoder
for anomaly detection; and a Monte Carlo conflict-probability estimator that samples from
the trajectory model's uncertainty.

"What's not built yet, and I'm specific about this rather than vague: the actual flagship
feature, the counterfactual simulator itself. I have the low-level primitives (a
deterministic event clock, a runway queueing model, scenario validation) and now the
delay-propagation model the simulator's design calls for it to couple to — but not the
engine that ties them together yet."

## Architecture — how to draw it on a whiteboard

Draw left to right:

```
[adsb.lol]  →  [ingest]  →  [Kafka/Redpanda]  →  [assembler]  →  [Redis pub/sub]
                   ↓                                  ↓                 ↓
              [Postgres+Timescale] ←――――――――――――――――――+          [api: /ws/live]
                   ↑                                  ↓                 ↓
              [api: REST]                        [MinIO/Parquet]   [frontend: deck.gl map]
                   ↑
              [frontend: REST fallback / track history / scorecard]
```

Narrate it in this order: external data source → two services that never talk to each
other directly, only through the queue → the two different "sinks" (durable DB vs.
disposable live cache vs. cold archive) → the API as the single point of contact with the
outside world → the frontend. If asked "why a queue between ingest and assembler instead of
calling it directly," the answer is: decoupling (one service's crash doesn't touch the
other), replay (any future consumer can read the full history from the beginning), and
at-least-once delivery with idempotent writes downstream so a crash mid-batch never
corrupts anything — full reasoning in [Architecture §2](ARCHITECTURE.md#2-the-same-shape-technically).

## Important technical decisions — questions and strong answers

**"Why Postgres instead of separate specialized databases for time-series and vector
data?"** One operational surface instead of three. TimescaleDB turns `state_vectors` into
an automatically-chunked, compressed, retention-managed hypertable with pre-computed
rollups — everything a dedicated time-series DB would give — and pgvector covers the one
similarity-search table (`trajectory_embeddings`) without standing up a fourth system for
one table. The tradeoff is real: a dedicated time-series DB would likely out-scale Postgres
eventually. At this project's current volume, that tradeoff hasn't mattered yet.

**"Why Redpanda instead of real Kafka?"** Wire-compatible, so nothing about the client code
changes if we ever move to real Kafka — but it's a single binary with no JVM or ZooKeeper to
operate, which matters a lot for a project meant to run as one Docker Compose stack on one
host.

**"Why a hand-rolled binary WebSocket protocol instead of JSON?"** Measured, not assumed:
JSON for thousands of aircraft at 1-2Hz is a genuine bandwidth and `JSON.parse` cost problem
in the browser. The binary format is 21 fixed bytes per record, decoded straight into typed
values with no parse pass over thousands of objects. The cost is that the frontend and
backend encoders have to be kept in sync by hand — there's no shared schema generating both
sides — which I called out explicitly as a real risk rather than a solved problem, and it's
exactly the kind of thing I now have round-trip tests protecting, added this session.

**"Why LightGBM instead of a neural network for the delay model?"** The feature set is
small and tabular — not sequential, not spatial, not high-dimensional. That's precisely the
regime where gradient-boosted trees reliably beat neural nets on both accuracy and training
time with far less hyperparameter sensitivity. A neural net is the right tool for M1
(trajectory forecasting), which genuinely is a sequence-modeling problem — I made that
distinction deliberately, not by default.

**"Why build the graph neural network from scratch instead of using PyTorch Geometric?"**
At this graph's size — about 334 airport nodes, two dense adjacency matrices — the actual
diffusion-convolution operation is a handful of matrix multiplications. Writing it directly
(`torch.einsum` over the adjacency and node features) kept the whole mechanism in about 100
lines I can walk through and explain exactly, instead of learning and depending on a
general-purpose library's API for something this small. If the graph were much larger or I
needed more exotic layer types, that tradeoff would flip.

**"Why did you train the trajectory and anomaly models on a different data source than
everything else?"** Because the honest answer was more valuable than papering over it: M1
and M4 need real continuous aircraft *position* history, which BTS (scheduled-flight
records) doesn't have at all, and this project's own live ingest hadn't been running long
enough to accumulate the volume a sequence model needs. Rather than train on too little
data or fake it, I used ADS-B Exchange's free historical sample data and wrote an ADR
explaining exactly why, what window I used, and what the honest limitation is.

**"Your anomaly detection result doesn't look very good — PR-AUC near chance. Why include
it?"** Because it's true, and I'd rather show a real negative result than a flattering fake
one. The test set only had 6 positive examples out of 1027 segments — nowhere near enough
to draw a real conclusion about whether the learned layer works, in either direction. I
documented that explicitly rather than cherry-picking a different cut of the data to make
the number look better, and the right fix (a larger, more varied evaluation sample) is in
my own prioritized future-work list, not hidden.

**"Your trajectory model loses to its own baseline. Doesn't that mean the GRU was a bad
idea?"** It means the baseline was underrated, not that the model was a bad idea. The spec
requires constant-velocity dead reckoning as the bar the GRU has to clear before it's worth
the extra complexity — I actually caught a real bug in my own baseline's velocity math first
(it was dividing a whole window's cumulative displacement by one 5-second step, overstating
speed ~11x, which is why the baseline originally *looked* easy to beat), fixed it, and the
corrected baseline turned out to be genuinely hard to beat at 60-300s: real aircraft in
cruise really are close to constant-velocity over that short a horizon. The GRU had one
hour of training data and ten epochs to learn to beat physics in a regime where physics
already wins, and it didn't get there. I reported that as the real result instead of
tuning until the comparison looked better, which is exactly the same standard I held the
anomaly result to.

## Difficult technical questions — honest answers specific to this project

**"How does authentication work?"** It doesn't, yet — every endpoint is read-only and
unauthenticated. That's a legitimate, current state, not an oversight I'm unaware of: there
are zero write endpoints in the system, so there's genuinely nothing to protect yet. I did
add a fail-fast check this session so the app refuses to start in a non-development
environment if the reserved write-auth key is still at its default — security posture
matched to the actual current attack surface, not theater for endpoints that don't exist.

**"How would this scale to 10x traffic?"** The first bottleneck would be `assembler` — it
runs as a single replica today with in-process per-aircraft state (open flight legs, known
aircraft types, last-sampled timestamps). The code's own comments are explicit that this is
a deliberate single-replica assumption; scaling out would mean externalizing that state,
likely to Redis, keyed by icao24 so partitioning stays consistent with Kafka's own
partitioning. The second bottleneck is `adsb.lol` itself — the real external rate limit,
which no amount of my own scaling fixes; a 10x-traffic answer here actually means "add a
second, paid data source," not "scale a service."

**"What happens if the database goes down?"** Right now: ingest's direct writes fail (but it
keeps publishing to Kafka, so no data is lost — assembler can replay once Postgres
recovers), and the API's `/health/ready` correctly reports it, but individual endpoint
calls would surface as generic 500s rather than a graceful degraded response. That's a real,
known gap I'd prioritize before calling this production-ready — a circuit breaker around
the DB calls in `api`, mirroring the one I already built for the external ADS-B feed.

**"Where are the bottlenecks?"** Today, honestly: adsb.lol's rate limit (external, not
fixable by better engineering on my side, confirmed live during testing — several tiles
returned 429s even at the already-reduced request rate), and the single-replica assembler
state. Neither is a problem at current traffic; both are the first things I'd point to if
asked to scale this up seriously.

**"What security vulnerabilities did you consider?"** No write endpoints means the
classical injection/IDOR/auth-bypass surface mostly doesn't exist yet — I didn't skip
building auth, there's genuinely nothing for it to protect. What I did find and fix: a
guessable default credential (`api_write_key`/`minio_secret_key`) that would've silently
worked in a real deployment if someone forgot to override it — now a fail-fast startup
check instead of a silent risk.

**"What was the hardest part?"** Not a code problem — a real-world constraint. The ADS-B
feed's rate limit wasn't documented anywhere; it took three rounds of empirical
investigation (visible in `docs/adr/0003`'s dated updates) to go from "it's a rate limit" to
"it's actually a request quota/budget," which changed the fix from "poll slower" to "poll
fewer, more valuable regions."

**"What would you change if rebuilding it?"** I'd build the live-staleness-window logic as
a single derived setting from day one instead of a constant duplicated in two files — that
exact duplication is what caused a real bug (the map going empty for most of every poll
cycle) that I only found by actually running the full stack end to end rather than trusting
the code at a glance. More generally: I'd put integration tests in CI from the first commit
— the isolation bug I found and fixed this session (tests leaking state into each other)
existed because nobody had run that suite together before, since CI never does.

**"What are the limitations?"** See [Learning guide §17](LEARNING_GUIDE.md#17-limitations-honestly)
for the full, honest list — the short version: five of six planned ML models are trained,
honestly evaluated, and live-served (M1-M5); the counterfactual simulator (the stated
flagship feature) still doesn't exist beyond two low-level primitives and M3's now-working
delay-propagation model, which the simulator's design calls for but hasn't yet been coupled
to a discrete-event engine; M3 has no live weather signal (no weather-ingestion pipeline
exists); M1 and M4 train on a historical data sample rather than this project's own
live-accumulated history; and there's no authentication (currently fine, given there's
still nothing to write).
