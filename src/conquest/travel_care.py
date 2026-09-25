"""Memory-driven healing and revival while a standalone route is moving."""

from conquest.character_context import farmer_name, state_path
from pathlib import Path
import time
import yaml

from conquest.addressing import WorkerPointerSession, PlayerLayout, resolve_player
from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader
from conquest.worker import request


class TravelStateChanged(ValueError):
    pass


# Consecutive display-panel closes that proved no button was pressed (hover
# race, busy input) before the original error is raised again.
CLOSE_NOT_READY_LIMIT = 3


class PanelTravelChanged(TravelStateChanged):
    code = "panel_intercepted"

    def __init__(self, panel):
        self.panel = panel
        super().__init__("Closed a shop panel; rechecking the route")


class TravelCare:
    def __init__(self, worker_info, notify=lambda event: None):
        self.info, self.notify = worker_info, notify
        health = request(worker_info, "health")
        from conquest.memory_build_layout import READ_LAYOUTS

        selected = READ_LAYOUTS.get(health.get("expected_sha256"))
        if selected is None:
            raise ValueError("Travel care has no exact layout for the attached client")
        root = Path("profiles")
        self.layout = PlayerLayout.model_validate(
            yaml.safe_load((root / selected.player_profile).read_text(encoding="utf-8"))
        )
        from conquest.memory_health import HealthWorkerSession

        self.session = HealthWorkerSession(worker_info, self.layout.expected_sha256)
        self.inventory = MemoryInventoryReader(
            self.session,
            self.layout,
            InventoryLayout.model_validate(
                yaml.safe_load(
                    (root / selected.inventory_profile).read_text(encoding="utf-8")
                )
            ),
        )
        self.health_layout = yaml.safe_load(
            (root / selected.health_profile).read_text(encoding="utf-8")
        )
        self.pending = None
        self.last_heal = -float("inf")
        self.last_revive = -float("inf")
        from conquest.memory_build_layout import CLIENT_SHA256_1078
        from conquest.discord_notify import read_json

        self.exact_1078 = selected.expected_sha256 == CLIENT_SHA256_1078
        self.revive_journal = Path(state_path(".runtime/travel-revive.json"))
        self.revive_state = (
            read_json(self.revive_journal, {}) if self.exact_1078 else {}
        )
        self.revive_living_samples = 0
        self.revive_last_sample = None

    def record_revive(self, phase, health, life, *, attempts=None):
        from conquest.discord_notify import write_json

        self.revive_state = {
            "phase": phase,
            "target": health["target"],
            "object_address": life["object_address"],
            "map_id": life["map_id"],
            "death_position": list(life["position"]),
            "updated_at": time.time(),
            "attempts": attempts
            if attempts is not None
            else self.revive_state.get("attempts", 0),
        }
        write_json(self.revive_journal, self.revive_state)

    @staticmethod
    def revive_rejected_before_input(error):
        from conquest.merchants.coordination import InputAcquisitionBusy

        message = str(error)
        return (
            isinstance(error, InputAcquisitionBusy)
            or message.startswith(
                (
                    "Revive pre-input rejected:",
                    "Mouse control is yours",
                    "Recovery waiting for game focus",
                    "The inspected ghost state is not present",
                    "Revive client geometry changed",
                    "Revive native viewport changed",
                    "Embedded Revive calibration changed",
                    "Stop farming before an input diagnostic",
                )
            )
            or "no button pressed" in message
            or "no input sent" in message
        )

    def close_not_ready(self, error):
        """Defer one unsent panel close to the next care pass, within a bound.

        True: the caller raises TravelStateChanged; the next pass runs the
        panel-clearing pass before any route input.  False: the bound is spent
        (or the failure is not provably unsent) and the error stays fatal.
        """
        from conquest.panel_events import panel_close_not_ready

        if not panel_close_not_ready(error):
            return False
        self.close_not_ready_count = getattr(self, "close_not_ready_count", 0) + 1
        if self.close_not_ready_count >= CLOSE_NOT_READY_LIMIT:
            return False
        self.next_panel_check = 0
        return True

    def check(self, health):
        if health["embedded_controls"].get("manual_mouse") or health[
            "embedded_controls"
        ].get("manual_input_fence"):
            raise TravelStateChanged("Mouse control is yours; waiting for idle")
        life = health["embedded_controls"].get("life")
        if life is None:
            return
        if health["embedded_controls"]["control"]["enabled"]:
            raise ValueError("Travel care cannot share input with farming")
        now = time.monotonic()
        if self.exact_1078 and self.revive_state.get("phase") in (
            "submitting",
            "uncertain",
            "submitted",
        ):
            saved = self.revive_state
            if saved.get("target") != health.get("target") or saved.get(
                "object_address"
            ) != life.get("object_address"):
                raise TravelStateChanged(
                    "Revive request belongs to a changed process or player; inspect before retrying"
                )
            if not life["dead_candidate"]:
                living = (
                    life.get("status", 0) & 0x420 == 0
                    and life.get("appearance") == 0
                    and life.get("current_hp", 0) > 0
                    and not life.get("ghost_candidate")
                )
                if not living:
                    raise TravelStateChanged(
                        "Waiting for native living state after Revive"
                    )
                if life["timestamp"] != self.revive_last_sample:
                    self.revive_last_sample = life["timestamp"]
                    self.revive_living_samples += 1
                if self.revive_living_samples < 3:
                    raise TravelStateChanged(
                        "Confirming Revive across fresh living observations"
                    )
                self.record_revive("verified", health, life)
            elif saved["phase"] in ("submitting", "uncertain"):
                raise TravelStateChanged(
                    "Revive input outcome is uncertain; no automatic repeat"
                )
            elif time.time() - saved.get("updated_at", time.time()) < 8:
                raise TravelStateChanged("Waiting for the submitted Revive result")
            elif saved.get("attempts", 0) >= 3:
                raise TravelStateChanged(
                    "Revive did not complete after three submitted attempts"
                )
        if life["dead_candidate"]:
            self.pending = None
            if life["revive_ready_candidate"] and now - self.last_revive >= 2:
                previous_attempts = (
                    self.revive_state.get("attempts", 0)
                    if self.revive_state.get("phase") == "submitted"
                    else 0
                )
                attempts = previous_attempts + 1 if self.exact_1078 else None
                if self.exact_1078:
                    self.record_revive("submitting", health, life, attempts=attempts)
                try:
                    request(
                        self.info,
                        "revive-click",
                        {
                            "health_profile": self.health_layout,
                            "character": farmer_name(),
                            "expected_size": health.get("window", {}).get(
                                "client_size", [1036, 793]
                            ),
                            "expires_at": time.time() + 4,
                            "input_mode": "foreground",
                        },
                    )
                except Exception as error:
                    if self.exact_1078:
                        self.record_revive(
                            "preinput_rejected"
                            if self.revive_rejected_before_input(error)
                            else "uncertain",
                            health,
                            life,
                            attempts=previous_attempts
                            if self.revive_rejected_before_input(error)
                            else attempts,
                        )
                    if self.revive_rejected_before_input(error):
                        raise TravelStateChanged(str(error)) from error
                    raise TravelStateChanged(
                        "Revive result is uncertain; no automatic repeat"
                    ) from error
                if self.exact_1078:
                    self.record_revive("submitted", health, life, attempts=attempts)
                self.last_revive = now
                self.notify(
                    {"event": "travel_revive", "death_position": life["position"]}
                )
            raise TravelStateChanged("Waiting for living route position")
        if now >= getattr(self, "next_panel_check", 0):
            if getattr(self, "panel_close_uncertain", False):
                raise ValueError(
                    "Town panel close remains uncertain; reconcile before route input"
                )
            self.next_panel_check = now + 1
            try:
                result = request(
                    self.info,
                    "town",
                    {"action": "clear-travel-panels", "expires_at": time.time() + 4},
                )
            except ValueError as error:
                from conquest.panel_events import panel_close_unverified

                if self.close_not_ready(error):
                    raise TravelStateChanged(
                        "Panel close was not ready (no button pressed); "
                        "retrying it before moving"
                    ) from error
                if not panel_close_unverified(error):
                    raise
                # The close may have been submitted. Do not issue the same
                # request again without a read-only panel-instance receipt.
                self.panel_close_uncertain = True
                raise TravelStateChanged(
                    "Rechecking an unconfirmed town panel close"
                ) from error
            self.panel_close_uncertain = False
            self.close_not_ready_count = 0
            if result.get("closed_panel"):
                self.notify(
                    {
                        "event": "travel_panel_closed",
                        "panel": result["closed_panel"],
                        "activity": "Closed "
                        + result["closed_panel"]
                        + " panel; continuing travel",
                    }
                )
                raise PanelTravelChanged(result["closed_panel"])
        if self.pending:
            before, hp, issued = self.pending
            after = self.inventory.read()
            if after.count(1000020) < before and life["current_hp"] > hp:
                self.notify(
                    {
                        "event": "travel_heal_verified",
                        "hp": life["current_hp"],
                        "potions": after.count(1000020),
                    }
                )
                self.pending = None
            elif now - issued < 2:
                return
            else:
                # A consumed potion can be masked by incoming damage. Do not
                # strand the character; continue escape and recheck after cooldown.
                self.notify(
                    {
                        "event": "travel_heal_unconfirmed",
                        "hp": life["current_hp"],
                        "consumed": after.count(1000020) < before,
                        "activity": "Healing result unclear; continuing toward safety",
                    }
                )
                self.pending = None
                self.last_heal = now
                return
        if life["current_hp"] >= life["max_hp"] * 0.75 or now - self.last_heal < 1:
            self.xp_step(health)
            return
        inventory = self.inventory.read()
        if inventory.count(1000020) <= 0:
            if not getattr(self, "empty_healing_reported", False):
                self.empty_healing_reported = True
                self.notify(
                    {
                        "event": "travel_healing_empty",
                        "activity": "No potions left; continuing toward town",
                    }
                )
            self.xp_step(health)
            return  # Keep escaping toward supplies; stopping cannot restore health.
        self.empty_healing_reported = False
        potion = next(
            i for i in inventory.items if i.type_id == 1000020 and i.amount > 0
        )
        masked = False
        deferred = None
        try:
            receipt = request(
                self.info,
                "town",
                {
                    "action": "consume-healing",
                    "uid": potion.uid,
                    "expires_at": time.time() + 4,
                },
            )
        except ValueError as error:
            from conquest.town_trade import TownObservationUnavailable

            if isinstance(error, TownObservationUnavailable) or str(error) in (
                "Game lost focus; no key sent",
                "Game did not receive focus; no key sent",
            ):
                raise TravelStateChanged(
                    "Regaining focus before travel healing"
                ) from error
            if (
                str(error) == "Healing consumption unverified; no repeat input issued"
                and self.inventory.read().count(1000020) == inventory.count(1000020) - 1
            ):
                self.last_heal = now
                self.notify(
                    {
                        "event": "travel_heal_unconfirmed",
                        "consumed": True,
                        "activity": "Potion consumed; continuing toward safety while checking HP",
                    }
                )
                masked = True
            else:
                raise
        finally:
            import sys

            failed = sys.exc_info()[0] is not None
            try:
                request(
                    self.info,
                    "town",
                    {
                        "action": "close",
                        "window": "Inventory",
                        "expires_at": time.time() + 4,
                    },
                )
            except (ValueError, OSError) as error:
                # The heal itself succeeded here.  A close proved unsent (e.g.
                # the pre-press hover race) leaves only an open Inventory:
                # retry it through the panel pass, never the potion.
                if not failed:
                    if not self.close_not_ready(error):
                        raise
                    deferred = error
            else:
                self.close_not_ready_count = 0
        if not masked:
            self.last_heal = now
            if receipt["consumed"]:
                self.notify(
                    {
                        "event": "travel_heal_verified",
                        "hp": receipt["hp_after"],
                        "potions": receipt["remaining"],
                    }
                )
        if deferred is not None:
            self.notify(
                {
                    "event": "travel_heal_close_deferred",
                    "detail": str(deferred),
                    "activity": "Healed; Inventory close was not ready, retrying it before moving",
                }
            )
            raise TravelStateChanged(
                "Inventory close after healing was not ready (no button pressed); "
                "retrying it before moving"
            ) from deferred

    def xp_step(self, health):
        # Normal travel uses the same memory-qualified popup as combat.
        # Avoid a remote GUI scan until the ready/flying status is present.
        life = health["embedded_controls"]["life"]
        status = life.get("status", 0)
        verifying = bool(
            status & 0x8000000
            and getattr(getattr(self, "_xp_skill", None), "pending", None)
        )
        if not status & 0x10 and not verifying:
            return
        from types import SimpleNamespace
        from conquest.xp_skill import XpSkill
        from conquest.memory_health import HealthWorkerSession, HealthLayout

        if not hasattr(self, "_xp_skill"):
            adapter = HealthWorkerSession(self.info, self.layout.expected_sha256)
            from conquest.memory_life import MemoryLifeReader

            observer = SimpleNamespace(
                adapter=adapter,
                health_layout=HealthLayout.model_validate(self.health_layout),
                character=farmer_name(),
                read_life=lambda: MemoryLifeReader.for_session(
                    adapter, farmer_name()
                ).read(),
            )
            self._xp_skill = XpSkill(
                observer, lambda event, fields: self.notify({"event": event, **fields})
            )

        def click(point):
            fresh = request(self.info, "health")["embedded_controls"]
            current = fresh.get("life")
            if (
                fresh["control"]["enabled"]
                or fresh.get("manual_mouse")
                or not current
                or current["dead_candidate"]
                or current["object_address"] != life["object_address"]
            ):
                raise TravelStateChanged("Travel XP input state changed")
            addresses = resolve_player(self.session, self.layout)
            request(
                self.info,
                "foreground-click",
                {
                    "point": list(point),
                    "button": "left",
                    "control": False,
                    "expected_size": health.get("window", {}).get(
                        "client_size", [1036, 793]
                    ),
                    "require_foreground": True,
                    "expires_at": time.time() + 4,
                    "guard": {
                        "name_address": hex(addresses["name"]),
                        "name": farmer_name(),
                        "hp_address": hex(addresses["max_hp"]),
                        "max_hp": current["max_hp"],
                    },
                },
            )

        from conquest.merchants.coordination import InputAcquisitionBusy

        try:
            activated = self._xp_skill.step(click)
        except InputAcquisitionBusy as error:
            # The coordinator denied entry before any XP input. Abandon this
            # care pass, not just the click: the route must recheck Stop/client
            # identity/life and plan again, then reread XP and its popup point.
            # Generic focus/OS/SendInput errors can be uncertain; never catch
            # them here or retry the current dispatch with its old evidence.
            raise TravelStateChanged(
                "XP input busy; reobserve before continuing travel"
            ) from error
        if activated:
            raise TravelStateChanged("XP full; activating Fly before continuing travel")
