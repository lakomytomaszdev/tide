"""Obsługa CLI pudełka wyłącznie w szeregowym wątku usługi."""
import json

from ..box import builder as bb, lifecycle, profile as bp, state as bs
from ..box.incus import IncusError
from . import boxstep


def target(engine, slug, card=""):
    from .engine import FlowError
    from ..board.model import card_num
    try:
        bp._check_slug(slug, "projekt")
    except bp.ProfileError as exc:
        raise FlowError("bad-project", str(exc)) from None
    board = engine.store.load()
    if slug not in board.projects:
        raise FlowError("no-project", f"nie ma projektu {slug}")
    if card:
        try:
            number = card_num(card)
        except (ValueError, TypeError):
            raise FlowError("bad-card", "oczekiwano identyfikatora K-N") from None
        item = board.cards.get(number)
        if item is None or item.project != slug:
            raise FlowError("box-scope", f"karta {card} nie należy do projektu {slug}")
        card = f"K-{number}"
    name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
    # Skracanie nazw Incusa nie może zmienić projektu operacji.
    for other in board.projects:
        if other != slug and bs.box_name(other) == name:
            raise FlowError("box-scope", "niejednoznaczna nazwa pudełka projektu")
    for num, item in board.cards.items():
        if item.project not in board.projects:
            continue
        if (item.project != slug or f"K-{num}" != card) and bs.card_box_name(item.project, f"K-{num}") == name:
            raise FlowError("box-scope", "niejednoznaczna nazwa pudełka karty")
    return card, name


def _require_owner(slug, card, name, inst):
    """Sama nazwa ani sam znacznik instancji nie są dowodem własności."""
    from .engine import FlowError
    config = (inst or {}).get("config") or {}
    stamp = bs.parse_stamp(config.get(bs.RECIPE_KEY))
    recipe = stamp.get("hash") if isinstance(stamp, dict) else stamp
    owned = bool(inst and recipe)
    if card:
        owned = owned and config.get(bs.COPY_OF_KEY) == bs.box_name(slug) and config.get(bs.CARD_KEY) == card
    else:
        owned = owned and not config.get(bs.COPY_OF_KEY) and not config.get(bs.CARD_KEY)
    known = ""
    try:
        with open(bs.journal_path(slug), encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if not isinstance(event, dict) or not event.get("type") or not event.get("box"):
                    raise ValueError("niepełne zdarzenie")
                if event["box"] != name:
                    continue
                if event["type"] in ("box.destroy", "box.build") or event.get("removed"):
                    known = ""
                if event["type"] == "box.ready" or (event["type"] == "box.build" and event.get("result") == "ok"):
                    known = event.get("hash") or ""
    except (OSError, ValueError, TypeError):
        owned = False
    if not owned or not known or known != recipe:
        raise FlowError("unknown-box-owner", f"{name}: nieznana własność zasobu — stop odmówiony")


def stop(engine, slug, card=""):
    from .engine import FlowError
    card, name = target(engine, slug, card)
    builder = boxstep.builder_of(engine, boxstep.settings_of(engine))
    # Ten sam lock co rezerwacja kroku, budowa i cykl życia. Kontrola obejmuje
    # także żywych klientów silnika, których nie widać jeszcze w box.use.
    # Zajęty projekt daje szybką odmowę, aby inne akcje kart nadal działały.
    try:
        with bs.lifecycle_lock(slug, blocking=False):
            _require_owner(slug, card, name, builder.incus.instance(name))
            duration = lifecycle.Lifecycle(builder).stop(slug, card or None)
    except bb.BoxBusy as exc:
        raise FlowError("box-busy", str(exc)) from None
    except (bb.BuildError, bp.ProfileError, IncusError, OSError, ValueError) as exc:
        raise FlowError("box-error", str(exc)) from None
    return {"ok": True, "slug": slug, "card": card, "box": name, "duration": duration}
