"""The staged first-migration rehearsal: the URL dry-run (ADR-0014 Validation).

The ADR lists two rehearsals before the production cutover. The
second-instance one (:mod:`rehearsal`, Validation (g)) walks the address
transitions, the kills and the socket's four facts on a running Caddy.
This one covers what that one explicitly does not: **what the site
answers**. A Caddyfile can migrate cleanly, hold its admin socket
correctly, pass every one of (g)'s sub-steps — and serve 404 on
``/lov/…``. Nothing else in the sequence looks at routing.

It is a *dry-run*: ``caddy validate`` and ``caddy adapt`` over the old and
the new Caddyfiles, against a fully built envelope and the flat release
still behind the ``lovspor-current`` symlink, and then a route-by-route
comparison of the two adapted configurations. Nothing is loaded, nothing
is reloaded, and nothing on disk is written — which the rehearsal itself
asserts at the end.

What "answers" means is :mod:`answers`' definition; what the dry-run does
with it is the ADR's four assertions, over three classes of URL, and two
preconditions those assertions rest on. Each is one step, in this order.
The URL set is derived from the *old* configuration — every file
under the trees its own adapted routes name, plus every path its own
matchers name — never from the new one, which would make the comparison
circular: a new configuration that stopped speaking about ``/robots.txt``
would simply stop being asked about it.

* ``staged.validate`` — the envelope is complete and Caddy accepts both
  files; nothing is compared before this.
* ``staged.host`` — both configurations match on the same host names. The
  walk treats a ``host`` matcher as satisfied, because it is asking about
  paths; that simplification is safe only while this holds, and a new
  site block on another name would otherwise answer every URL here and
  none of them on the box.
* ``staged.corpus`` — every URL matching ADR-0013's corpus paths that the
  old configuration answers must get the **same response** from the new
  one, served from exactly ``<release>/corpus``, and every Caddy snippet
  the new configuration imports from inside the deployment root must live
  inside the release envelope: that is "under the new release's map", and
  without it a fragment could serve the release's pages under the old
  tree's redirects and the response comparison would not notice.
* ``staged.proxied`` — every URL the old configuration answers by proxy
  keeps the same upstream. The migration moves no part of the app surface,
  so this is an equality that costs nothing and catches a rewritten
  ``@app`` matcher.
* ``staged.site`` — ``/`` and ``/observatory/``, plus every URL the old
  configuration served from outside the corpus, must be answered by
  ``file_server`` from exactly ``<release>/site``. Their *bytes* are not
  compared: the migrated site is a rebuild of the hand-written landing
  page, and the Observatory golden test is what compares its text.
* ``staged.symlinks`` — no serving path of the new configuration passes
  through a symlink, below the deployment root: not a root it names, and
  not a file it resolves. This is the assertion that catches a
  configuration which works today and rots later — one ``ln -sfn`` away
  from serving a different release, with every URL assertion still
  passing. It reads the adapted routes, so a symlink arriving through an
  imported fragment is as visible as one written in the file.
* ``staged.rollback`` — the previous Caddyfile, adapted again afterwards,
  answers **exactly** as it did over the whole question set: no answer
  lost, none changed, and none *gained*, roots included, since it is the
  same configuration. It names no Unix-socket admin endpoint, and every
  tree the dry-run read is byte-identical to how it found it. The
  one-directional allowance of ``staged.corpus`` — the new configuration
  answering more than the old is legitimate, the site tree grew — has no
  counterpart here: a rollback that answers *more* has not put the host
  back, it has produced a third state nobody specified.

Nothing here can run in CI: there is no ``caddy`` binary on the runner, so
CI proves only that this module is sound against the committed captures.
The run that authorises the migration is the one on the droplet, beside
the second-instance rehearsal — both, never either.
"""

import hashlib
import os
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from pathlib import Path

from lovspor.release.answers import (
    INDEX_FILE,
    NOT_FOUND,
    Answer,
    answer_for,
    hidden_paths,
    hosts,
    is_servable_url,
    matcher_paths,
    matches_path,
    roots,
)
from lovspor.release.caddy import Runner, adapt_config, admin_listen
from lovspor.release.caddy import validate as validate_caddy
from lovspor.release.envelope import (
    CORPUS_DIR,
    CORPUS_PATHS,
    FRAGMENT_NAME,
    SITE_DIR,
    missing_parts,
)
from lovspor.release.errors import CommitRefusedError, RehearsalFailedError
from lovspor.release.rehearsal import RehearsalReport, Step

OBSERVATORY_URL = "/observatory/"
SITE_URLS = ("/", OBSERVATORY_URL)
"""The two the ADR names by hand; the rest of the site class comes off the old trees."""
PROBE_SEGMENT = "lovspor-dry-run-probe"
"""What a ``*`` in a matcher is asked about: a name no tree holds.

A wildcard cannot be enumerated, so the dry-run asks one URL inside the
namespace. Where that namespace is a redirect map's 410 it answers, and
the new configuration is held to the same answer; where it is a file
namespace nothing is there, so the old answers nothing and the URL drops
out of the comparison on its own.
"""
_UNIX_PREFIX = "unix/"
PROGRESS_INTERVAL = 30.0
"""The fewest seconds between two lines saying how far one pass has got.

Time rather than a URL count: the droplet's rate is exactly what is not
known in advance, and a count picked on a laptop is a line a second on one
machine and a line an hour on another.
"""


def _silent(line: str) -> None:
    """Progress a caller did not ask for goes nowhere."""


@dataclass(frozen=True)
class Progress:
    """Where the dry-run says how far it has got, and the clock it measures that by.

    Never part of the report: the report is what the run found, printed once
    it has found it — and until then a run over the droplet's corpus printed
    nothing, so eleven hours of work read exactly like a hang (#307).
    """

    say: Callable[[str], None] = _silent
    clock: Callable[[], float] = time.monotonic

    def counted(self, label: str, urls: Sequence[str]) -> Iterator[str]:
        """``urls`` one at a time: the pass's start, then how far it has got each interval."""
        self.say(f"staged: asking {label}")
        started = said = self.clock()
        for done, url in enumerate(urls, start=1):
            yield url
            now = self.clock()
            if now - said >= PROGRESS_INTERVAL:
                said = now
                self.say(_how_far(label, done, len(urls), now - started))


def _how_far(label: str, done: int, total: int, elapsed: float) -> str:
    """Answered so far, the rate, and the rest of the pass at that rate.

    Only reached an interval or more into a pass, so ``elapsed`` is never zero.
    """
    rate = done / elapsed
    rest = timedelta(seconds=round((total - done) / rate))
    return f"staged: {label} {done}/{total} URLs, {rate:.1f}/s, ETA {rest}"


@dataclass(frozen=True)
class StagedPlan:
    """The two Caddyfiles, the envelope they are compared against, the deployment root
    it stands in, how to run Caddy, and where to say how far the run has got.

    ``deployment`` is ``/var/www``: the boundary of the import assertion and of
    the symlink assertion. Above it the symlinks belong to the machine
    (``/var`` is one on a Mac), and the deployment neither made them nor can
    move them; below it they are the deployment's own. It is handed in and has
    no default, because where the release stands says nothing about it:
    ``rehearse-urls.sh`` builds its envelope three levels down, and a root
    derived as the release's grandparent let an import of the old tree's
    redirect map pass unchecked (#308).
    """

    runner: Runner
    previous: Path
    proposed: Path
    release: Path
    deployment: Path
    progress: Progress = field(default_factory=Progress)

    @property
    def corpus(self) -> Path:
        return self.release / CORPUS_DIR

    @property
    def site(self) -> Path:
        return self.release / SITE_DIR

    @property
    def fragment(self) -> Path:
        return self.release / FRAGMENT_NAME


@dataclass(frozen=True)
class Reading:
    """Both configurations as Caddy adapts them, what each answers, and the trees as found.

    ``taken`` is read after the previous Caddyfile is adapted and BEFORE
    the proposed one is: it is the reading the untouched-files assertion
    compares against, so anything the dry-run's own second half writes has
    to fall on this side of it.

    ``urls`` is the set of questions asked, kept because it is not
    recoverable from ``before``: a URL the old configuration answers
    nothing for is absent there, and the rollback has to be able to tell
    "it answered nothing, as before" from "it was never asked".
    """

    previous: object
    proposed: object
    before: Mapping[str, Answer]
    after: Mapping[str, Answer]
    trees: tuple[Path, ...]
    taken: tuple[tuple[str, str], ...]
    urls: tuple[str, ...]


def _require(held: bool, step: str, detail: str) -> None:
    """The dry-run's one refusal: a named sub-step and what did not hold."""
    if not held:
        raise RehearsalFailedError(step, detail)


def digests(trees: Sequence[Path]) -> tuple[tuple[str, str], ...]:
    """``(path, sha256)`` for every file under each tree, sorted; the untouched-files reading."""
    taken: list[tuple[str, str]] = []
    for tree in trees:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                taken.append((path.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(sorted(taken))


def symlinked_component(path: Path, boundary: Path) -> Path | None:
    """The first symlink on ``path`` at or below ``boundary``, or ``None``.

    A path that never reaches ``boundary`` is refused rather than passed:
    a serving root outside the deployment root is not something the
    dry-run can make any claim about.
    """
    components = [path, *path.parents]
    if boundary not in components:
        raise RehearsalFailedError(
            "staged.symlinks", f"{path} is outside the deployment {boundary}"
        )
    return next((one for one in components[: components.index(boundary)] if one.is_symlink()), None)


def tree_urls(root: Path) -> tuple[str, ...]:
    """Every URL the files under ``root`` offer; ``index.html`` is its directory.

    A name that is not a servable URL is skipped rather than asked about:
    the evaluator would refuse it, and a dry-run failing on its own
    question says nothing about either configuration. ADR-0013's publish
    check refuses such a slug, so a corpus that reached here cannot hold
    one — this is the guarantee, not a silent repair of a broken tree.
    """
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.name == INDEX_FILE:
            parent = relative.parent.as_posix()
            found.append("/" if parent == "." else f"/{parent}/")
        else:
            found.append(f"/{relative.as_posix()}")
    return tuple(url for url in found if is_servable_url(url))


def _as_url(pattern: str) -> str | None:
    """The one URL a matcher path can be asked about, or ``None`` when it is not one.

    A wildcard anywhere but the end names a set no single URL stands for;
    a trailing one names a namespace, and the probe asks inside it.
    Whatever comes out is held to the evaluator's own rules, so a matcher
    the dry-run cannot ask about is dropped here and never refused later.
    """
    if "*" in pattern[:-1]:
        return None
    url = pattern[:-1] + PROBE_SEGMENT if pattern.endswith("*") else pattern
    return url if is_servable_url(url) else None


def matcher_urls(patterns: Sequence[str]) -> tuple[str, ...]:
    """Each matcher path as a URL: literal as it stands, a trailing ``*`` as one probe."""
    return tuple(url for pattern in patterns if (url := _as_url(pattern)) is not None)


def candidate_urls(config: object) -> tuple[str, ...]:
    """Every URL ``config`` could answer: its own trees' files and its own matchers' paths.

    Read off the configuration the migration is leaving, so that what the
    new one is asked about cannot be narrowed by the new one itself.
    """
    found: list[str] = []
    for root in roots(config):
        tree = Path(root)
        if tree.is_dir():
            found.extend(tree_urls(tree))
    found.extend(matcher_urls(matcher_paths(config)))
    return tuple(dict.fromkeys(found))


def answers_for(config: object, urls: Iterable[str]) -> dict[str, Answer]:
    """What ``config`` answers for each URL; a URL no route reaches is left out."""
    found: dict[str, Answer] = {}
    for url in urls:
        answer = answer_for(config, url)
        if answer is not None:
            found[url] = answer
    return found


def _answered(answer: Answer) -> bool:
    """A 404 is not an answer; a 301 and a 410 are, and the new configuration owes both."""
    return answer.status != NOT_FOUND


def _in(path: str | None, tree: Path) -> bool:
    """Whether ``path`` names ``tree``, following the deployment's own symlinks.

    Resolved on purpose: a root reaching ``<release>/corpus`` through a
    symlink serves the right tree today, which is exactly why forbidding
    it belongs to the symlink assertion and not to this one. Each negative
    fixture then fails at one step, and its refusal says which.
    """
    return path is not None and Path(path).resolve() == tree.resolve()


def unreadable_reason(path: Path) -> str | None:
    """Why the dry-run cannot read ``path``, or ``None``; a directory is said to be one.

    Separated from the raising so the refusal is composed in one place:
    three reasons through three ``raise`` sites means three copies of the
    sub-step's name, and only one of the three is reachable without a
    particular identity — the other two would then be pinned by nothing
    the gate can measure on every machine.
    """
    if path.is_dir():
        return "is a directory"
    if not path.is_file():
        return "is not a file"
    if not os.access(path, os.R_OK):
        return "is not readable"
    return None


def _readable(path: Path, role: str) -> None:
    """A file the dry-run must read, named here rather than through ``caddy``'s stderr.

    ``caddy validate`` on a missing or unopenable file reports a read error
    the operator then has to interpret; the seam this rehearsal is built on
    puts what decides in Python.
    """
    broken = unreadable_reason(path)
    if broken is not None:
        raise RehearsalFailedError("staged.validate", f"the {role} {path} {broken}")


def _inside_the_deployment(plan: StagedPlan) -> None:
    """The release stands below the deployment root, before anything of it is read.

    As spelled, because that is how the symlink assertion reads its boundary
    off the adapted roots; and as resolved, because a lexical child reached
    through a symlink is read from wherever that symlink points. A root the
    release is not under would leave the release's own trees beyond the line
    both boundary assertions check inside.
    """
    spelled = plan.deployment in plan.release.parents
    _require(
        spelled and plan.deployment.resolve() in plan.release.resolve().parents,
        "staged.validate",
        f"the release {plan.release} is outside the deployment root {plan.deployment}",
    )


def validated(plan: StagedPlan) -> Step:
    """The envelope is complete and Caddy accepts both files; nothing is compared before this."""
    _inside_the_deployment(plan)
    missing = missing_parts(plan.release)
    _require(
        not missing, "staged.validate", f"{plan.release.name} is incomplete: {', '.join(missing)}"
    )
    _readable(plan.previous, "previous Caddyfile")
    _readable(plan.proposed, "proposed Caddyfile")
    for caddyfile in (plan.previous, plan.proposed):
        try:
            validate_caddy(plan.runner, caddyfile, plan.fragment)
        except CommitRefusedError as error:
            raise RehearsalFailedError("staged.validate", str(error)) from error
    return Step(
        name="staged.validate",
        detail=f"caddy validate accepts {plan.previous.name} and {plan.proposed.name}",
    )


def _class_of(reading: Reading, corpus: bool) -> tuple[str, ...]:
    """The URLs the old configuration answers by file, on one side of the corpus matcher."""
    return tuple(
        url
        for url, answer in reading.before.items()
        if _answered(answer)
        and answer.handler != "reverse_proxy"
        and matches_path(CORPUS_PATHS, url) == corpus
    )


def _compared(reading: Reading, url: str, step: str) -> Answer:
    """The new configuration's answer, required to be the same response as the old's."""
    old, new = reading.before[url], reading.after.get(url)
    if new is None or not new.same_response(old):
        raise RehearsalFailedError(
            step,
            f"{url}: the old answers {old.describe()}, the new "
            f"{new.describe() if new is not None else 'nothing'}",
        )
    return new


def _maps_are_the_releases_own(plan: StagedPlan, reading: Reading) -> None:
    """Every snippet the new configuration imports from the deployment is inside the release.

    Caddy records each imported file in the ``hide`` list of the
    ``file_server`` beside it, so this reads the adapted routes. A
    fragment serving the release's pages under the *old* tree's redirect
    map would compare equal on every response — the old map is what the
    old configuration answered with — and is caught only here.
    """
    release = plan.release.resolve()
    for hidden in hidden_paths(reading.proposed):
        parents = Path(hidden).resolve().parents
        _require(
            plan.deployment.resolve() not in parents or release in parents,
            "staged.corpus",
            f"the new configuration imports {hidden}, which is not the release's own",
        )


def hosts_agree(reading: Reading) -> Step:
    """Both site blocks match on the same names; the walk's host simplification rests on it."""
    before, after = hosts(reading.previous), hosts(reading.proposed)
    _require(
        before == after,
        "staged.host",
        f"the old configuration serves {before or 'no host'} and the new {after or 'no host'}",
    )
    return Step(name="staged.host", detail=f"both configurations serve {', '.join(before)}")


def corpus_preserved(plan: StagedPlan, reading: Reading) -> Step:
    """Every corpus URL the old answers, answered the same by the new from ``<release>/corpus``."""
    urls = _class_of(reading, corpus=True)
    _require(bool(urls), "staged.corpus", "the old configuration answers no corpus URL at all")
    for url in urls:
        new = _compared(reading, url, "staged.corpus")
        _require(
            new.handler != "file_server" or _in(new.root, plan.corpus),
            "staged.corpus",
            f"{url} is served from {new.root}, not from {plan.corpus}",
        )
    _maps_are_the_releases_own(plan, reading)
    return Step(name="staged.corpus", detail=f"{len(urls)} corpus URLs answered from {plan.corpus}")


def proxied_preserved(reading: Reading) -> Step:
    """The app surface the old configuration proxies keeps its upstream."""
    urls = tuple(url for url, answer in reading.before.items() if answer.handler == "reverse_proxy")
    for url in urls:
        _compared(reading, url, "staged.proxied")
    return Step(name="staged.proxied", detail=f"{len(urls)} proxied URLs keep their upstream")


def site_answered(plan: StagedPlan, reading: Reading) -> Step:
    """``/``, ``/observatory/`` and the rest of the old site, answered from ``<release>/site``."""
    urls = tuple(dict.fromkeys((*SITE_URLS, *_class_of(reading, corpus=False))))
    for url in urls:
        new = reading.after.get(url)
        if new is None or new.handler != "file_server" or not _answered(new):
            raise RehearsalFailedError(
                "staged.site",
                f"{url}: the new configuration answers "
                f"{new.describe() if new is not None else 'nothing'}",
            )
        _require(
            _in(new.root, plan.site),
            "staged.site",
            f"{url} is served from {new.root}, not from {plan.site}",
        )
    return Step(name="staged.site", detail=f"{len(urls)} site URLs answered from {plan.site}")


def _serving_paths(reading: Reading) -> tuple[Path, ...]:
    """Every path the new configuration serves through: its roots, and the files it resolves."""
    found = [Path(root) for root in roots(reading.proposed)]
    found.extend(
        Path(answer.root) / answer.served
        for answer in reading.after.values()
        if answer.root is not None and answer.served is not None
    )
    return tuple(dict.fromkeys(found))


def no_symlink_served(plan: StagedPlan, reading: Reading) -> Step:
    """No root the new configuration names, and no file it resolves, passes through a symlink."""
    paths = _serving_paths(reading)
    for path in paths:
        found = symlinked_component(path, plan.deployment)
        _require(
            found is None,
            "staged.symlinks",
            f"{path} is served through the symlink {found}, which a publish can move",
        )
    return Step(name="staged.symlinks", detail=f"{len(paths)} serving paths, no symlink on any")


def _shown(answer: Answer | None) -> str:
    """An answer, or the absence of one — which is a value here and not a gap."""
    return answer.describe() if answer is not None else "nothing"


def _first_difference(before: Mapping[str, Answer], after: Mapping[str, Answer]) -> str | None:
    """The first URL, by name, the two readings disagree about — in either direction.

    Over the union of both key sets, so a URL that has *gained* an answer
    is a difference and not an absence. Ordered by the URL so the line an
    operator reads is the same on every machine.
    """
    for url in sorted({*before, *after}):
        old, new = before.get(url), after.get(url)
        if old != new:
            return f"{url}: {_shown(new)}, not {_shown(old)}"
    return None


def _changed(taken: Sequence[tuple[str, str]], now: Sequence[tuple[str, str]]) -> str:
    difference = sorted(set(taken) ^ set(now))
    return difference[0][0] if difference else "nothing"


def _still_answers(plan: StagedPlan, reading: Reading, again: object) -> None:
    """Exactly what it answered: no answer lost, none changed, and none gained."""
    asked = plan.progress.counted("the previous configuration again", reading.urls)
    difference = _first_difference(reading.before, answers_for(again, asked))
    _require(
        difference is None,
        "staged.rollback",
        f"the previous configuration no longer answers as it did — {difference}",
    )


def rollback_restores(plan: StagedPlan, reading: Reading) -> Step:
    """The way back: the same answers, the admin endpoint on TCP, the files as they were."""
    again = adapt_config(plan.runner, plan.previous, plan.fragment)
    listen = admin_listen(again)
    _require(
        listen is None or not listen.startswith(_UNIX_PREFIX),
        "staged.rollback",
        f"the previous Caddyfile binds the admin endpoint to {listen}, not to TCP",
    )
    _still_answers(plan, reading, again)
    now = digests(reading.trees)
    _require(
        now == reading.taken,
        "staged.rollback",
        f"{_changed(reading.taken, now)} changed under the dry-run",
    )
    return Step(
        name="staged.rollback", detail=f"the old answers restored, {len(now)} files as found"
    )


def _read_trees(plan: StagedPlan, previous: object) -> tuple[Path, ...]:
    """What the dry-run reads and must leave alone: the envelope and the old serving trees."""
    trees = [plan.release, *(Path(root) for root in roots(previous))]
    return tuple(tree for tree in dict.fromkeys(trees) if tree.is_dir())


def _read(plan: StagedPlan) -> Reading:
    """Both Caddyfiles adapted, both asked the same questions, the trees read in between."""
    previous = adapt_config(plan.runner, plan.previous, plan.fragment)
    trees = _read_trees(plan, previous)
    urls = tuple(dict.fromkeys((*candidate_urls(previous), *SITE_URLS)))
    plan.progress.say(f"staged: {len(urls)} URLs to ask each configuration")
    before = answers_for(previous, plan.progress.counted("the previous configuration", urls))
    taken = digests(trees)
    proposed = adapt_config(plan.runner, plan.proposed, plan.fragment)
    after = answers_for(proposed, plan.progress.counted("the proposed configuration", urls))
    return Reading(previous, proposed, before, after, trees, taken, urls)


def _assertions(plan: StagedPlan, reading: Reading) -> tuple[tuple[str, Callable[[], Step]], ...]:
    """The assertions over the reading, in the ADR's order, each by the name it reports under."""
    return (
        ("staged.host", partial(hosts_agree, reading)),
        ("staged.corpus", partial(corpus_preserved, plan, reading)),
        ("staged.proxied", partial(proxied_preserved, reading)),
        ("staged.site", partial(site_answered, plan, reading)),
        ("staged.symlinks", partial(no_symlink_served, plan, reading)),
        ("staged.rollback", partial(rollback_restores, plan, reading)),
    )


def staged_rehearsal(plan: StagedPlan) -> RehearsalReport:
    """ADR-0014's staged first-migration rehearsal: the URL half, in its order."""
    plan.progress.say("staged: checking staged.validate")
    steps = [validated(plan)]
    reading = _read(plan)
    for name, check in _assertions(plan, reading):
        plan.progress.say(f"staged: checking {name}")
        steps.append(check())
    return RehearsalReport(steps=tuple(steps))
