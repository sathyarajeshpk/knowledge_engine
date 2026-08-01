# Learning Guide — M1: Discovery

**Assumed:** you can read Python, write a function, use a list and a dict.
**Assumed from M0:** modules, dataclasses, enums, type hints, `frozen=True`.
**Goal:** understand every concept M1 uses, from first principles, with examples
you can run.

Each section is: **the problem** → **the mechanism** → **how we use it** → **the
trap**.

---

## 1. Protocols — describing a shape, not a family tree

### The problem

Discovery needs to fetch web pages. In production that means real HTTP. In tests
it must mean "hand back this string I prepared earlier", because tests that touch
the network are slow, flaky, and impossible on the day the source is down.

So `HtmlTableSource` must accept *something that fetches*, without caring what.

The classic answer is a base class:

```python
class Fetcher(ABC):
    @abstractmethod
    def fetch(self, url): ...

class HttpFetcher(Fetcher): ...
class FakeFetcher(Fetcher): ...   # must inherit to qualify
```

That works, but it demands **inheritance**. Every test double must import your
base class and subclass it. The relationship is nominal: you qualify by
declaring parentage.

### The mechanism

A `Protocol` describes the **shape** an object must have. Anything with a
matching method qualifies — no inheritance, no import, no registration.

```python
from typing import Protocol

class Greeter(Protocol):
    def greet(self, name: str) -> str: ...

class Friendly:                       # inherits nothing
    def greet(self, name): return f"Hi {name}"

def welcome(g: Greeter) -> str:
    return g.greet("Ada")

welcome(Friendly())        # 'Hi Ada' — type checkers accept this
```

This is "static duck typing": *if it walks like a duck, it is a duck* — checked
at type-check time rather than discovered at runtime.

### How we use it

```python
class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...
```

In `engine/tests/test_discovery.py`, `FakeFetcher` inherits from nothing:

```python
class FakeFetcher:
    def __init__(self, body=None, error=None): ...
    def fetch(self, url) -> FetchResult: ...
```

And some doubles are three lines defined inside the test that needs them:

```python
class Exploding:
    def fetch(self, url):
        raise RuntimeError("unexpected adapter bug")
```

That is the payoff. The cost of a new test double is a class with one method.

`Clock` works the same way — `now()`, `today()`, `run_id()`.

### The trap

A Protocol is **not enforced at runtime**. If you pass an object without `fetch`,
nothing complains until the call happens. Protocols catch mistakes in a type
checker, not in the interpreter. Use them for seams you control; use real checks
for data arriving from outside.

---

## 2. Dependency injection — passing in what you depend on

### The problem

The obvious way to write an adapter:

```python
class HtmlTableSource:
    def discover(self):
        html = urlopen(self.url).read()      # reaches out and grabs it
        today = date.today()                 # asks the system what day it is
```

Both lines look harmless. Both make the class untestable. You cannot run this
without the internet, and you cannot run it twice and get the same answer.

### The mechanism

**Don't fetch your dependencies — receive them.**

```python
class HtmlTableSource:
    def __init__(self, definition, fetcher, clock, *, max_summary_words=120):
        self._fetcher = fetcher      # given, not created
        self._clock = clock
```

Production passes `HttpFetcher()` and `SystemClock()`. Tests pass `FakeFetcher()`
and `FrozenClock()`. The class cannot tell, and does not care.

### Why the clock matters *permanently* here

`discovered_date` feeds Feature ID minting when a publication date is missing.
Feature IDs are permanent. So "what time is it?" is a question with irreversible
consequences — and a test must be able to answer it differently from production:

```python
CLOCK = FrozenClock(datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc))
assert CLOCK.today() == date(2026, 8, 2)      # true today, true in ten years
```

We enforce this rather than trusting it. There is a test that walks every file in
`engine/ke/` looking for `datetime.now(` or `date.today(`:

```python
def test_no_engine_module_reads_the_clock_directly():
    for path in engine.rglob("*.py"):
        if path.name == "clock.py": continue
        assert "datetime.now(" not in path.read_text()
```

**A rule nobody checks is a rule that decays.** This one is checked.

### The trap

Injection can be overdone. Inject things that are **slow, external, or
non-deterministic** — network, clock, randomness, filesystem. Injecting a pure
function like `slugify` buys nothing and costs clarity.

---

## 3. Purity and determinism

### The problem

Two runs of the same code over the same input should produce the same bytes. If
they do not, you cannot tell a real change from noise, and every diff becomes
suspicious.

### The mechanism

A **pure function** depends only on its arguments and changes nothing outside
itself.

```python
def double(x):  return x * 2          # pure

total = 0
def add(x):                            # impure: reads and writes outside state
    global total
    total += x
    return total
```

Pure functions are trivially testable: no setup, no teardown, no mocks.

### How we use it

Every function in `normalize.py` is pure. No clock, no network, no state. That is
what makes discovery replayable: given the same recorded bytes and a frozen
clock, the whole pipeline produces identical output.

### The hidden non-determinism: iteration order

Sets and dicts can iterate in orders that surprise you, and "surprise" in a file
that gets committed means a spurious diff every week. So ordering is always made
explicit:

```python
# canonical_url: query parameters SORTED, so ?a=1&b=2 and ?b=2&a=1 agree
kept = sorted((k, v) for k, v in parse_qsl(parts.query) ...)

# sort_items: an explicit total order
key = (item.published_date is None, item.published_date, item.identity.key)
```

Read that tuple carefully — it is a neat trick. `published_date is None` is a
**bool**, and `False < True`, so dated items sort before undated ones. Then date,
then identity as a tiebreaker so the order is *total*: no two items can compare
equal and be left in arbitrary order.

### Try it

```python
sorted([(True, 'x'), (False, 'a')])     # [(False, 'a'), (True, 'x')]
```

---

## 4. Hashing for identity

### The problem

Two table rows are on a page. Are they the same update? They carry no ID. Next
week the wording changes. Same update or new one?

Get this wrong and the engine mints a second permanent Feature ID for knowledge
it already has — forever, because IDs are never reused.

### The mechanism

A **hash function** maps any input to a fixed-size string, deterministically:

```python
import hashlib
hashlib.sha256(b"hello").hexdigest()
# 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
```

Same input → same hash, always. Different input → different hash (in practice).

The important consequence: **the hash is only as stable as what you feed it.**
Hashing raw text means any wording change creates a new identity. So the work is
in *normalising* the input first.

### How we use it: normalise, then hash

```python
def normalise_title(title):
    words = re.sub(r"[^a-z0-9 ]", " ", title.lower()).split()
    words = [w for w in words if w not in TITLE_NOISE]
    return " ".join(sorted(words))        # ← sorted!
```

Sorting the words is the trick that makes these agree:

```
"Announcing general availability of Direct Lake"  → "direct lake"
"Direct Lake is now generally available"          → "direct lake"
```

Both drop the noise words (`announcing`, `general`, `availability`, `is`, `now`,
`generally`, `available`, `of`) and sort what remains.

### The trap, and the honest limitation

Word-order-insensitive matching **increases collisions**. These normalise
identically:

```
"Direct Lake supports Warehouse"
"Warehouse supports Direct Lake"
```

And the noise list is a judgement call. Adding a word makes more things match —
including things that should not. We accept a *missed* match over a *wrong*
match, because a missed match creates a duplicate you can see, and a wrong match
silently merges two different features.

This is why the title hash is **third** in the hierarchy:

| Rank | Basis | Why here |
|---|---|---|
| 1 | canonical URL | survives rewording *and* reordering |
| 2 | source identifier | stable, but opaque to humans |
| 3 | normalised title | fragile to verb changes |
| 4 | content fingerprint | changes when anything changes |

And there is a test named `test_title_identity_does_not_survive_a_verb_change`
that **asserts the limitation**, so nobody later mistakes it for a bug and
"fixes" it by growing the noise list.

> Pinning a known limitation with a test is a real technique. A test can say
> "this is deliberate" far more durably than a comment.

---

## 5. Parsing HTML with a state machine

### The problem

You need the rows out of the tables on a web page. Regex is the wrong tool — HTML
nests, and nesting is exactly what regex cannot track.

### The mechanism

Python's `html.parser.HTMLParser` is a **streaming** parser: it walks the
document and calls your methods as it encounters things.

```python
from html.parser import HTMLParser

class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href"))

c = Collector()
c.feed('<p>Hi <a href="/x">there</a></p>')
print(c.links)      # ['/x']
```

Streaming means you never hold a tree in memory — but it also means **you** must
remember where you are.

### The mechanism: a state machine

"Where am I?" is tracked with flags:

```python
self._in_heading = False
self._in_row     = False
self._in_cell    = False
self._cell_buf   = []
```

`handle_starttag` sets them, `handle_data` appends to whichever buffer is active,
`handle_endtag` clears them and commits the collected text.

Why a buffer rather than a single string? Because `handle_data` can fire **many
times** for one cell — entities, nested tags, and chunk boundaries all split it:

```html
<td>Direct <b>Lake</b> GA</td>
<!-- handle_data fires 3 times: "Direct ", "Lake", " GA" -->
```

Collecting into a list and joining at the end is the only correct way.

### The whitespace idiom

You will see this everywhere in the codebase:

```python
" ".join("".join(self._cell_buf).split())
```

Read it inside out: join the fragments → `.split()` with no argument (splits on
*any* whitespace run and drops empties) → rejoin with single spaces. Result:
newlines, tabs and runs of spaces all collapse to one space.

```python
" ".join("  Direct\n\n  Lake  ".split())     # 'Direct Lake'
```

That matters because `content_hash` fingerprints the text. Without collapsing,
Microsoft reflowing a paragraph would look like a knowledge change.

---

## 6. Regular expressions, and anchoring

### The problem

Find a date in a table cell.

### The mechanism

```python
import re
m = re.search(r"(?P<year>\d{4})-(?P<month>\d{2})", "released 2026-07-15")
m["year"]      # '2026'
```

`(?P<name>...)` is a **named group**, readable at the use site — `m["year"]` beats
`m.group(1)`.

### `search` vs `match` — the distinction that mattered

| | Behaviour |
|---|---|
| `re.search(p, s)` | find `p` **anywhere** in `s` |
| `re.match(p, s)` | match `p` at the **start** of `s` |
| `re.match(r"^...$", s)` | the whole string, and nothing else |

### The real bug this caused

The adapters originally searched each row for a date. Against the live page:

```
| Data Factory gateway manual update (Preview) | The Gateway
  December 2025 release adds a manual update option. |
```

`search` found "December 2025" — **inside a sentence** — and labelled it `EXACT`.
That row would have been stamped with a publication month scraped from prose, and
a **permanent** Feature ID minted from it. One row in 361: rare, silent, and
impossible to fix afterwards.

The fix is anchoring:

```python
DATE_ONLY_CELL = re.compile(
    r"^\s*(?:(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<my>(?:January|...|December)\s+\d{4}))\s*$",   # ← ^ and $
    re.IGNORECASE)

def parse_date_cell(cell):
    if not DATE_ONLY_CELL.match(cell or ""):
        return None, DatePrecision.DAY, DateConfidence.INFERRED
    return parse_date(cell)
```

Now only a cell containing *nothing but* a date is trusted.

### Order matters too

`parse_date` tries most precise first. `"July 15, 2026"` also contains
`"July 2026"`, so matching the looser pattern first would silently discard the
day. There is a test for exactly this.

---

## 7. Two ideas that look like one: precision and confidence

### The problem

The source says `July 2026`. What do you store?

Two *different* questions are hiding here:

1. **How precise is it?** A month, not a day.
2. **How much do you trust it?** Completely — the source stated it.

Squeezing both into one field forces a lie. "Month precision" is not
"uncertain" — it is exactly, confidently, a month.

### The mechanism

Two independent fields:

```python
date_precision:  DAY | MONTH | YEAR        # how sharp
date_confidence: EXACT | INFERRED          # how trusted
```

They combine freely:

| Source text | precision | confidence | Meaning |
|---|---|---|---|
| `2026-07-15` | `day` | `exact` | stated exactly |
| `July 2026` | `month` | `exact` | a confidently-known month |
| `Wave 2026` | `year` | `inferred` | might be a copyright notice |
| nothing | `day` | `inferred` | fell back to discovery date |

`published_date` still holds a real `date` (`2026-07-01` for a month) so ordering
stays simple — `date_precision` says how much of it to believe.

> **The general lesson:** when one field is being asked to answer two questions,
> the answer is usually two fields. Overloading is how "exact" ends up meaning
> "we guessed but confidently".

---

## 8. Path arithmetic with `posixpath`

### The problem

A Markdown file at `docs/fundamentals/whats-new.md` links to
`../onelake/direct-lake-ga.md#modes`. What URL does that render as?

### The mechanism

`posixpath` does forward-slash path arithmetic — always, on every OS. That is why
we use it rather than `os.path`: URLs use `/` even on Windows.

```python
import posixpath
posixpath.dirname("docs/fundamentals/whats-new.md")     # 'docs/fundamentals'
posixpath.join("docs/fundamentals", "../onelake/x.md")  # 'docs/fundamentals/../onelake/x.md'
posixpath.normpath("docs/fundamentals/../onelake/x.md") # 'docs/onelake/x.md'
```

`join` is naive; `normpath` resolves the `..` segments. You need both.

### How we use it

```python
path = posixpath.normpath(
    posixpath.join(posixpath.dirname(doc_path), target.split("#", 1)[0]))
if docs_prefix and path.startswith(docs_prefix):
    path = path[len(docs_prefix):]              # strip 'docs/'
if path.startswith("../"):
    return None                                  # escaped the docs root
return canonical_url(f"{base_url}/{path[:-3]}")  # drop '.md'
```

### The trap, and the design decision

`normpath` can produce a path that climbs *above* where you started:

```python
posixpath.normpath("docs/../../secret.md")      # '../secret.md'
```

We return `None` there rather than pressing on. That is a deliberate choice with
a real reason:

> A fabricated URL would be **worse** than no URL. Identity would report basis
> `canonical-url` — the strongest, most durable basis — while resting on an
> invention. The Feature ID minted from it is permanent.

Returning `None` drops to a weaker basis, which is *honest*. When the choice is
between looking confident and being right, pick right.

---

## 9. Exceptions as control flow, and when to catch broadly

### The problem

A source is down. What should the code do?

### The mechanism: a custom exception type

```python
class SourceError(Exception):
    """Anything that stopped a source producing items."""
```

A dedicated type means callers can catch *this* without accidentally swallowing
`KeyboardInterrupt`, `MemoryError`, or a typo-induced `AttributeError`.

### The design: raise vs return empty

Look at the adapter:

```python
if not parser.rows:
    raise SourceError("no table rows found; the page structure has changed")
```

Why raise instead of returning `[]`? Because **an empty list is a lie here**.
Zero rows from a page that should be full of them is a parser break, not a quiet
week — and the two must never be indistinguishable. Silent data loss for weeks is
the failure mode this whole engine exists to prevent.

### The broad catch, and why it is right here

Normally `except Exception:` is a smell — it hides bugs. In `discover.py` it is
deliberate:

```python
except SourceError as exc:
    return failed_attempt(str(exc)), [], str(exc)
except Exception as exc:                          # noqa: BLE001
    reason = f"adapter error: {type(exc).__name__}: {exc}"
    return failed_attempt(reason), [], reason
```

Two catches, two meanings. `SourceError` is "the source failed" — expected.
Bare `Exception` is "our adapter has a bug" — unexpected, and **still must not end
the run**. A crash in one adapter would otherwise take every other source down
with it, and suppress the run-log commit that stops GitHub auto-disabling the
weekly cron after 60 quiet days.

The rule:

> Catch broadly only where you have a **specific** reason the process must
> survive, and record what you caught. Never catch broadly to make a symptom go
> away.

Note it is recorded, not swallowed — `failure_reason` carries the exception type
and message into the run report.

---

## 10. The median, and why not the mean

### The problem

A source that returns 20 items every week returns 1 this week. Broken, or quiet?

### The mechanism

```python
recent = [20, 22, 19, 21]
mean   = sum(recent) / len(recent)              # 20.5
median = sorted(recent)[len(recent) // 2]       # 21
```

(That one-liner is the idea, not the implementation. A true median averages the
two middle values on an even-length list, which is what `baseline_items` in
`models.py` actually does — for `[19, 20, 21, 22]` it returns `20.5`.)

They look similar — until an outlier arrives:

```python
recent = [20, 22, 19, 21, 400]     # one bad week: a page duplicated its table
mean   = 96.4                       # wrecked
median = 21                         # unmoved
```

The mean is dragged by any extreme value. The median only cares about the middle.

### How we use it

Health compares this run's count against the median of recent runs. One anomalous
week — a duplicated section, a partial page — does not poison the baseline for
weeks afterwards.

> **The general lesson:** for a "is this normal?" baseline over noisy real-world
> data, reach for the median first. The mean is for when you actually want the
> total divided by the count.

---

## 11. Enums that mean different things

M1 added several enums that look similar and are not. Understanding why each is
separate *is* understanding the discovery chain.

```python
AdapterType          # the CODE that read it        html, markdown, rss, atom…
SourceRepresentation # the FORMAT received          html, markdown, rss, atom, api
ExtractionMethod     # the STRATEGY used            html-table-row, feed-entry…
IdentityBasis        # what the ID RESTS ON         canonical-url, title-hash…
SourceRole           # WHERE in the fallback chain  primary, secondary, manual-review
SourceStatus         # LIFECYCLE of the definition  active, deprecated, disabled…
HealthState          # CURRENT condition            healthy, degraded, failed…
```

### "Isn't representation the same as adapter?"

Usually, yes — and that is the point of asking. They diverge the moment one
source has two representations:

```
Microsoft Learn "What's New"
├── rendered HTML   → HtmlTableSource      (adapter=html,     representation=html)
└── source Markdown → MarkdownTableSource  (adapter=markdown, representation=markdown)
        ↑ same knowledge, different host, different failure mode
```

`FeedSource` already serves three adapter types by itself. The adapter name
cannot answer "was this read from the rendered page or the source file?" once a
source grows a second way in — and that question is exactly what an
investigation years later will ask.

### Status vs health — a distinction worth internalising

- **`SourceStatus`** is a *decision a human made*: this source is retired.
- **`HealthState`** is an *observation the engine made*: this source is failing.

A source can be `active` and `failed` (should work, doesn't) or `deprecated` and
`healthy` (works fine, we've moved on). Collapsing them would make "we turned it
off" and "it broke" indistinguishable.

---

## 12. Findings over exceptions — two error styles, on purpose

M0's validator **returns** problems. M1's adapters **raise** them. That is not
inconsistency; it is two different situations.

| | Style | Why |
|---|---|---|
| `validate.py` | return `list[Finding]` | You want **all** the problems at once. Raising on the first would mean fixing them one run at a time. |
| Adapters | raise `SourceError` | The first failure means there is nothing to continue with. |
| `discover.py` | catch, record, continue | One source's failure must not affect another's. |

The rule of thumb:

> **Raise** when the current operation cannot meaningfully continue.
> **Return findings** when you want a complete picture.
> **Catch and record** when you are the one responsible for surviving.

---

## 13. Putting it together

Follow one table row all the way through:

```
<tr><td>July 2026</td>
    <td><a href="/en-us/fabric/direct-lake-ga">Direct Lake GA</a></td>
    <td>Now generally available.</td></tr>
```

1. **`Fetcher`** (injected §2) returns the page body.
2. **`WhatsNewParser`** (§5) walks it, tracking section and cells with a state
   machine, buffering text and collapsing whitespace.
3. **`parse_date_cell`** (§6) sees `"July 2026"` is a date-*only* cell → accepts
   it → `date(2026,7,1)`, precision `month`, confidence `exact` (§7).
4. **`canonical_url`** (§3) resolves and normalises the link, stripping tracking
   parameters and sorting what remains.
5. **`compute_identity`** (§4) has a URL, so basis is `canonical-url` — durable.
6. **`truncate_summary`** caps the summary at the pack's word limit (copyright).
7. A **`RawItem`** is built carrying a **`Provenance`** record naming every link
   in the chain (§11).
8. **`sort_items`** (§3) gives a deterministic order.
9. **`discover.py`** records a healthy `SourceAttempt` and moves on.

And if step 1 had raised `SourceError`? The chain (§9) falls to the Markdown
secondary, health becomes `degraded`, the run continues — and if *every* link
failed, a `ReviewItem` is produced, because "no updates" and "we could not look"
must never look the same.

---

## 14. Concept index

| Concept | Section | Where in the code |
|---|---|---|
| Protocol / structural typing | 1 | `clock.py`, `sources/base.py` |
| Dependency injection | 2 | every adapter's `__init__` |
| Purity and determinism | 3 | all of `normalize.py` |
| Explicit sort keys | 3 | `sort_items`, `canonical_url` |
| Hashing for identity | 4 | `identity.py` |
| Normalise-then-hash | 4 | `normalise_title` |
| Streaming parser + state machine | 5 | `html_table.py` |
| Whitespace collapsing idiom | 5 | everywhere text is read |
| Regex anchoring | 6 | `DATE_ONLY_CELL` |
| Named groups | 6 | `normalize.py` patterns |
| Orthogonal fields | 7 | `date_precision` / `date_confidence` |
| POSIX path arithmetic | 8 | `resolve_doc_link` |
| Refusing to guess | 8 | `resolve_doc_link` returning `None` |
| Custom exceptions | 9 | `SourceError` |
| Justified broad catch | 9 | `discover._attempt` |
| Median vs mean | 10 | `SourceHealth.record` |
| Enums with distinct meanings | 11 | `models.py` |
| Findings vs exceptions | 12 | `validate.py` vs adapters |
