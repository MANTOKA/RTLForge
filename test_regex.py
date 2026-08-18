import re

VERILATOR_RE = re.compile(
    r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Z0-9_]+))?:\s*(?P<file>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<msg>.*)"
)


test = "%Error-WIDTH: counter.v:10:5: Width mismatch"

m = VERILATOR_RE.match(test)

if m:
    print(m.groupdict())
else:
    print("No match")