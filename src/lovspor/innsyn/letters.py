"""The innsynskrav letter, in bokmål or nynorsk (issue #509, point 4).

The wording is the pilot's (``draft_letters.py`` as changed on 2026-10-06,
and the letters sent on 2026-10-05), with two additions the pilot taught:

* up front, the request is for the authority's **own archive documents** —
  the vedtak with the adopted text and the text sent to Norsk Lovtidend — and
  explicitly not Lovdata's consolidations (Oslo answered "it is on Lovdata");
* a **general point** for every other local regulation in force, so an
  authority whose pages linked no LF id is asked too.

offentleglova § 28 andre ledd: a request "må gjelde ei bestemt sak eller i
rimeleg utstrekning saker av ein bestemt art". The known ids (part a) and the
general request (part b) are separate numbered points, each said to be a
request of its own, so a refusal of the general point as too wide cannot take
the specific one with it. The general point is phrased as cases of one kind
(the authority's own local regulations in force), asks for an overview, and
invites narrowing rather than a refusal. Electronic copies: § 30 første ledd.
"""

from pydantic import BaseModel, ConfigDict

from lovspor.innsyn.directory import Maalform
from lovspor.innsyn.population import Authority
from lovspor.innsyn.sender import Sender

#: The one kommune whose regulations are adopted by a bystyre, not a kommunestyre.
_BYSTYRE = frozenset({"0301"})


class LetterRequest(BaseModel):
    """Everything one letter is made from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority: Authority
    recipient: str
    maalform: Maalform
    lf_ids: tuple[str, ...]
    sender: Sender


class Letter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recipient: str
    subject: str
    body: str

    def text(self) -> str:
        """The file form the owner sends from: ``Til:``, ``Emne:``, then the body."""
        return f"Til: {self.recipient}\nEmne: {self.subject}\n\n{self.body}"


class _Words(BaseModel):
    """The nouns a letter varies by authority kind and målform."""

    model_config = ConfigDict(frozen=True)

    the: str  # kommunen / fylkeskommunen
    owns: str  # kommunens / kommunen sine
    organ: str  # kommunestyret / fylkestinget


def _words(authority: Authority, maalform: Maalform) -> _Words:
    the = "kommunen" if authority.kind == "kommune" else "fylkeskommunen"
    owns = f"{the}s" if maalform == "nb" else f"{the} sine"
    if authority.authority_id in _BYSTYRE:
        organ = "bystyret"
    else:
        organ = "kommunestyret" if authority.kind == "kommune" else "fylkestinget"
    return _Words(the=the, owns=owns, organ=organ)


def id_line(lf_id: str) -> str:
    year, month, day, number = lf_id.split("-")
    return f"- FOR-{lf_id} (dato {day}.{month}.{year}, nr. {number})"


_NB = {
    "intro": (
        "Jeg ber om innsyn etter offentleglova § 3 i dokumenter om lokale forskrifter som "
        "{name} har fastsatt."
    ),
    "two": (
        "Kravet har to nummererte punkter. Hvert punkt er et selvstendig krav og bør vurderes for "
        "seg; skulle det ene bli avslått, ber jeg om at det andre likevel blir behandlet."
    ),
    "archive": (
        "Jeg ber ikke om Lovdatas konsoliderte tekster, men om {owns} egne arkivdokumenter: "
        "vedtaket i {organ} (eller annet vedtaksorgan) med den vedtatte forskriftsteksten, og "
        "teksten slik den ble oversendt til kunngjøring i Norsk Lovtidend. Teksten på Lovdata er "
        "Lovdatas redaksjonelle gjengivelse; jeg trenger kildedokumentene fra {owns} arkiv."
    ),
    "known_head": "1. Forskrifter med kjent nummer",
    "known": (
        "Forskriftene nedenfor er oppgitt med Lovdata-/Lovtidend-nummer (dato og nummer), slik de "
        "er lenket fra {owns} egne nettsider. For hver av dem ber jeg om forskriften slik den ble "
        "vedtatt, og alle senere forskrifter om endring av den. Dersom et nummer er ukjent for "
        "{the}, eller forskriften ikke er fastsatt av {the}, ber jeg om at dere opplyser om det i "
        "stedet for å sende dokumenter."
    ),
    "general_head_after": "2. Andre lokale forskrifter",
    "general_head_alone": "1. Lokale forskrifter",
    "general_after": (
        "Jeg ber også om innsyn i saker av en bestemt art (offentleglova § 28 andre ledd): alle "
        "andre lokale forskrifter som {the} har fastsatt og som er i kraft, med alle forskrifter "
        "om endring av dem. For hver av dem ber jeg om de samme dokumentene som i punkt 1."
    ),
    "general_alone": (
        "Jeg ber om innsyn i saker av en bestemt art (offentleglova § 28 andre ledd): alle lokale "
        "forskrifter som {the} har fastsatt og som er i kraft, med alle forskrifter om endring av "
        "dem. For hver av dem ber jeg om forskriften slik den ble vedtatt, og hver forskrift om "
        "endring av den."
    ),
    "overview": (
        "Jeg ber i tillegg om en liste eller oversikt over disse forskriftene. Mener dere at "
        "punktet ikke er tilstrekkelig avgrenset, ber jeg om at dere sender oversikten og tar "
        "kontakt om avgrensning, i stedet for å avslå."
    ),
    "form": (
        "Jeg ber om dokumentene i elektronisk form (PDF eller annet digitalt format) per e-post, "
        "jf. offentleglova § 30 første ledd."
    ),
    "free": (
        "Dokumentene vil bli gjort fritt tilgjengelige. Lover, forskrifter og andre vedtak av "
        "offentlig myndighet er uten opphavsrettslig vern etter åndsverkloven § 14."
    ),
    "thanks": "Takk for hjelpen.",
    "greeting": "Med vennlig hilsen",
}

_NN = {
    "intro": (
        "Eg ber om innsyn etter offentleglova § 3 i dokument om lokale forskrifter som "
        "{name} har fastsett."
    ),
    "two": (
        "Kravet har to nummererte punkt. Kvart punkt er eit sjølvstendig krav og bør vurderast "
        "for seg; skulle det eine bli avslått, ber eg om at det andre likevel blir handsama."
    ),
    "archive": (
        "Eg ber ikkje om Lovdata sine konsoliderte tekstar, men om {owns} eigne arkivdokument: "
        "vedtaket i {organ} (eller anna vedtaksorgan) med den vedtekne forskriftsteksten, og "
        "teksten slik han vart send til kunngjering i Norsk Lovtidend. Teksten på Lovdata er "
        "Lovdata si redaksjonelle attgjeving; eg treng kjeldedokumenta frå arkivet til {the}."
    ),
    "known_head": "1. Forskrifter med kjent nummer",
    "known": (
        "Forskriftene nedanfor er oppgitt med Lovdata-/Lovtidend-nummer (dato og nummer), slik "
        "dei er lenka frå {owns} eigne nettsider. For kvar av dei ber eg om forskrifta slik ho "
        "vart vedteken, og alle seinare forskrifter om endring av henne. Dersom eit nummer er "
        "ukjent for {the}, eller forskrifta ikkje er fastsett av {the}, ber eg om at de opplyser "
        "om det i staden for å sende dokument."
    ),
    "general_head_after": "2. Andre lokale forskrifter",
    "general_head_alone": "1. Lokale forskrifter",
    "general_after": (
        "Eg ber òg om innsyn i saker av ein bestemt art (offentleglova § 28 andre ledd): alle "
        "andre lokale forskrifter som {the} har fastsett og som er i kraft, med alle forskrifter "
        "om endring av dei. For kvar av dei ber eg om dei same dokumenta som i punkt 1."
    ),
    "general_alone": (
        "Eg ber om innsyn i saker av ein bestemt art (offentleglova § 28 andre ledd): alle lokale "
        "forskrifter som {the} har fastsett og som er i kraft, med alle forskrifter om endring av "
        "dei. For kvar av dei ber eg om forskrifta slik ho vart vedteken, og kvar forskrift om "
        "endring av henne."
    ),
    "overview": (
        "Eg ber i tillegg om ei liste eller oversikt over desse forskriftene. Meiner de at "
        "punktet ikkje er godt nok avgrensa, ber eg om at de sender oversikta og tek kontakt om "
        "avgrensing, i staden for å avslå."
    ),
    "form": (
        "Eg ber om dokumenta i elektronisk form (PDF eller anna digitalt format) per e-post, "
        "jf. offentleglova § 30 første ledd."
    ),
    "free": (
        "Dokumenta vil bli gjort fritt tilgjengelege. Lover, forskrifter og andre vedtak av "
        "offentleg styresmakt er utan opphavsrettsleg vern etter åndsverklova § 14."
    ),
    "thanks": "Takk for hjelpa.",
    "greeting": "Med venleg helsing",
}

#: The pilot's subject line, en dash included.
_SUBJECT = "Innsynskrav etter offentleglova \N{EN DASH} lokale forskrifter i {name}"


def _intro(phrases: dict[str, str], request: LetterRequest) -> list[str]:
    first = phrases["intro"]
    if request.lf_ids:
        first = f"{first} {phrases['two']}"
    return [first, phrases["archive"]]


def _points(phrases: dict[str, str], request: LetterRequest) -> list[str]:
    if not request.lf_ids:
        return [phrases["general_head_alone"], phrases["general_alone"], phrases["overview"]]
    ids = "\n".join(id_line(lf_id) for lf_id in request.lf_ids)
    known = [phrases["known_head"], phrases["known"], ids]
    return [*known, phrases["general_head_after"], phrases["general_after"], phrases["overview"]]


def render(request: LetterRequest) -> Letter:
    """The letter for ``request``; a pure function of it, so a wave is reproducible."""
    phrases = _NB if request.maalform == "nb" else _NN
    name = request.authority.letter_name
    words = _words(request.authority, request.maalform).model_dump()
    closing = [phrases["form"], phrases["free"], phrases["thanks"]]
    paragraphs = [*_intro(phrases, request), *_points(phrases, request), *closing]
    text = [paragraph.format(name=name, **words) for paragraph in paragraphs]
    signature = f"{phrases['greeting']}\n{request.sender.name}\n{request.sender.email}"
    body = "Hei,\n\n" + "\n\n".join([*text, signature]) + "\n"
    return Letter(recipient=request.recipient, subject=_SUBJECT.format(name=name), body=body)
