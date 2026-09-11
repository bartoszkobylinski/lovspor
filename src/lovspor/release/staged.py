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
with it is the ADR's four assertions, one step each, over three classes of
URL. The URL set is derived from the *old* configuration — every file
under the trees its own adapted routes name, plus every path its own
matchers name — never from the new one, which would make the comparison
circular: a new configuration that stopped speaking about ``/robots.txt``
would simply stop being asked about it.

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
  answers exactly as it did (roots included: it is the same
  configuration), names no Unix-socket admin endpoint, and every tree the
  dry-run read is byte-identical to how it found it.

Nothing here can run in CI: there is no ``caddy`` binary on the runner, so
CI proves only that this module is sound against the committed captures.
The run that authorises the migration is the one on the droplet, beside
the second-instance rehearsal — both, never either.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lovspor.release.answers import (
    NOT_FOUND,
    Answer,
    answer_for,
    hidden_paths,
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


@dataclass(frozen=True)
class StagedPlan:
    """The two Caddyfiles, the envelope they are compared against, and how to run Caddy."""

    runner: Runner
    previous: Path
    proposed: Path
    release: Path

    @property
    def corpus(self) -> Path:
        return self.release / CORPUS_DIR

    @property
    def site(self) -> Path:
        return self.release / SITE_DIR

    @property
    def fragment(self) -> Path:
        return self.release / FRAGMENT_NAME

    @property
    def deployment(self) -> Path:
        """The directory the releases root sits in — ``/var/www``.

        The symlink assertion's boundary. Above it the symlinks belong to
        the machine (``/var`` is one on a Mac), and the deployment neither
        made them nor can move them; below it they are the deployment's
        own, which is what the ADR is about.
        """
        return self.release.parent.parent


@dataclass(frozen=True)
class Reading:
    """Both configurations as Caddy adapts them, what each answers, and the trees as found.

    ``taken`` is read after the previous Caddyfile is adapted and BEFORE
    the proposed one is: it is the reading the untouched-files assertion
    compares against, so anything the dry-run's own second half writes has
    to fall on this side of it.
    """

    previous: object
    proposed: object
    before: Mapping[str, Answer]
    after: Mapping[str, Answer]
    trees: tuple[Path, ...]
    taken: tuple[tuple[str, str], ...]


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
    """Every URL the files under ``root`` offer; ``index.html`` is its directory."""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.name == "index.html":
            parent = relative.parent.as_posix()
            found.append("/" if parent == "." else f"/{parent}/")
        else:
            found.append(f"/{relative.as_posix()}")
    return tuple(found)


def matcher_urls(patterns: Sequence[str]) -> tuple[str, ...]:
    """Each matcher path as a URL: literal as it stands, a trailing ``*`` as one probe."""
    found = [
        pattern[:-1] + PROBE_SEGMENT if pattern.endswith("*") else pattern
        for pattern in patterns
        if "*" not in pattern[:-1]
    ]
    return tuple(found)


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


def answers_for(config: object, urls: Sequence[str]) -> dict[str, Answer]:
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


def validated(plan: StagedPlan) -> Step:
    """The envelope is complete and Caddy accepts both files; nothing is compared before this."""
    missing = missing_parts(plan.release)
    _require(
        not missing, "staged.validate", f"{plan.release.name} is incomplete: {', '.join(missing)}"
    )
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


def _first_difference(before: Mapping[str, Answer], after: Mapping[str, Answer]) -> str:
    for url, answer in before.items():
        if after.get(url) != answer:
            found = after.get(url)
            return f"{url}: {found.describe() if found else 'nothing'}, not {answer.describe()}"
    return f"{len(after) - len(before)} URLs it did not answer before"


def _changed(taken: Sequence[tuple[str, str]], now: Sequence[tuple[str, str]]) -> str:
    difference = sorted(set(taken) ^ set(now))
    return difference[0][0] if difference else "nothing"


def _still_answers(reading: Reading, restored: Mapping[str, Answer]) -> None:
    _require(
        restored == dict(reading.before),
        "staged.rollback",
        "the previous configuration no longer answers as it did — "
        + _first_difference(reading.before, restored),
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
    _still_answers(reading, answers_for(again, tuple(reading.before)))
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
    before, taken = answers_for(previous, urls), digests(trees)
    proposed = adapt_config(plan.runner, plan.proposed, plan.fragment)
    return Reading(previous, proposed, before, answers_for(proposed, urls), trees, taken)


def staged_rehearsal(plan: StagedPlan) -> RehearsalReport:
    """ADR-0014's staged first-migration rehearsal: the URL half, in its order."""
    steps = [validated(plan)]
    reading = _read(plan)
    steps.append(corpus_preserved(plan, reading))
    steps.append(proxied_preserved(reading))
    steps.append(site_answered(plan, reading))
    steps.append(no_symlink_served(plan, reading))
    steps.append(rollback_restores(plan, reading))
    return RehearsalReport(steps=tuple(steps))
