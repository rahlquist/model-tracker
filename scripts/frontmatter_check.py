"""Manual §2 frontmatter checklist for SKILL.md. skills-ref was unavailable,
so this verifies every hard frontmatter rule programmatically."""
import re
import sys

SKILL = "/home/rahlquist/model-tracker/SKILL.md"
text = open(SKILL, encoding="utf-8").read()

m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
assert m, "No frontmatter block found"
fm = m.group(1)

def get(key):
    mm = re.search(rf"^{key}:\s*(.*)$", fm, re.MULTILINE)
    return mm.group(1).strip() if mm else None

problems = []

# name
name = get("name")
if name != "model-tracker":
    problems.append(f"name must be 'model-tracker', got '{name}'")
if not re.fullmatch(r"[a-z0-9-]{1,64}", name or ""):
    problems.append("name has invalid chars / length")

# description
desc = get("description")
if not desc:
    problems.append("description missing")
elif len(desc) > 1024:
    problems.append(f"description too long: {len(desc)}")
elif not re.search(r"track|log|rate|rank|session stats|model", desc, re.I):
    problems.append("description missing trigger keywords / what+when")

# compatibility
comp = get("compatibility")
if not comp or "Python 3.10" not in comp:
    problems.append("compatibility missing Python 3.10+")

# metadata version
ver = get("metadata")
# metadata is a block; find version separately
vm = re.search(r"version:\s*\"?([0-9.]+)\"?", fm)
if not vm or vm.group(1) != "1.0":
    problems.append("metadata.version must be '1.0'")

# no other frontmatter keys
allowed = {"name", "description", "compatibility", "metadata"}
keys = re.findall(r"^([a-z_]+):", fm, re.MULTILINE)
extra = [k for k in keys if k not in allowed]
if extra:
    problems.append(f"unexpected frontmatter keys: {extra}")

# body length
body = text[m.end():]
nlines = body.count("\n") + 1
ntokens = len(body.split())
if nlines >= 500:
    problems.append(f"body too long: {nlines} lines")
if ntokens >= 5000:
    problems.append(f"body too many tokens: {ntokens}")

print(f"name: {name}")
print(f"description len: {len(desc) if desc else 0} (<=1024)")
print(f"compatibility: {comp}")
print(f"metadata.version: {vm.group(1) if vm else None}")
print(f"frontmatter keys: {keys}")
print(f"body lines: {nlines} (<500), tokens: {ntokens} (<5000)")

if problems:
    print("\n[FAIL] FRONTMATTER CHECKLIST:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("\n[PASS] T8 SKILL.md frontmatter passes every §2 rule; body < 500 lines.")
