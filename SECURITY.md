# Security

Do not open public issues containing API keys, license files, private RTL or raw
provider requests. Report a sensitive issue through GitHub private vulnerability
reporting if enabled; otherwise contact the repository owner privately.

The web workbench is a local development interface, not an authenticated multi-user
service. Keep it bound to loopback. Docker EDA uses no network, a read-only filesystem,
an unprivileged user and resource limits. Optional Vivado runs locally through the
official MCP server; only executor-generated Tcl is submitted.

These boundaries reduce exposure but are not a formal sandbox-security guarantee.
Treat untrusted RTL/TB as executable input and use an isolated development machine
when evaluating hostile workloads. Keys belong in ignored `.env` files or process
environment variables.
