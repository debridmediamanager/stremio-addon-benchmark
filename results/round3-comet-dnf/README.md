# Comet round 3 DNF diagnostic

This is a one-title diagnostic, not a fifth field row. Comet `feat/usenet` at
`ed1ede74` was built from source, reset to empty state, configured with the same
two parity indexers, provider account, 6 GiB pick cap and 10-connection pool as
round 3, and run alone on the same Hetzner VM after the publishable field was
complete.

It returned 188 candidates in 1.629 seconds and resolved its selected result in
0.114 seconds, then served a complete 421,667-byte status video instead of the
film. The process opened zero NNTP sockets because its native capability
preflight failed before playback. A configured pool of 10 cannot fill when the
reader never attempts a provider connection, so this is a DNF rather than an
exact-parity throughput sample.

The resource monitor still captured the failed attempt: 1.12 CPU seconds,
1.088 p95 cores, 486.42 MB peak RSS, 0.00 MB physical reads, 0.89 MB physical
writes and 0.00 MB retained-state growth. These one-title figures are evidence
for the failure path and are not ranked against the 23-title field phase.
