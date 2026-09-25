"""Persist a death location, verify revival, then return along checked terrain."""

from conquest.character_context import installation_path, state_path
import json
from pathlib import Path
import time

from conquest.navigation import native_waypoint
from conquest.capture import CaptureUnavailable


class RouteRecovery:
    def __init__(
        self, control, identity, terrain, dispatch, checkpoint, *, clock=time.monotonic
    ):
        self.control, self.identity, self.terrain, self.dispatch = (
            control,
            identity,
            terrain,
            dispatch,
        )
        self.path, self.clock = Path(checkpoint), clock
        self.episode = None
        self.pending = None
        self.healthy_samples = 0
        self.last_sample = None
        self.enabled = True
        self.avoided = set()
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if saved.get("identity") == identity and saved.get("phase") not in (
                "completed",
                "cancelled",
            ):
                self.episode = saved
                self.avoided = set(map(tuple, saved.get("avoided_tiles", [])))
                if saved.get("phase") == "returning_after_revive" and saved.get(
                    "next_waypoint"
                ):
                    self.pending = (
                        tuple(saved["next_waypoint"]),
                        self.clock(),
                        tuple(saved["last_verified_position"]),
                    )
                if saved.get("phase") in (
                    "recovery_uncertain",
                    "revive_submitting",
                    "verifying_revive",
                ):
                    self.episode["phase"] = "recovery_uncertain"
                else:
                    self.episode["phase"] = "checking_recovery"

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.episode, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def cancel(self):
        if self.episode:
            self.episode["phase"] = "cancelled"
            self.save()
        self.episode = self.pending = None
        self.healthy_samples = 0

    def status(self, state, note):
        return {
            "state": state,
            "note": note,
            "death_position": self.episode["death_position"] if self.episode else None,
        }

    def issue(self, kind, target, life, revision):
        try:
            return self.dispatch(kind, target, life, revision)
        except CaptureUnavailable:
            # Pre-input guards rejected this action. Observe afresh next tick.
            return False
        except Exception:
            self.episode["phase"] = "recovery_uncertain"
            self.save()
            raise

    def step(self, life, focused):
        if not self.enabled:
            return None
        intent = self.control.snapshot()
        if not intent["enabled"]:
            return None  # Off pauses recovery, including after an app reload.
        if life is None or not 0 <= self.clock() - life["timestamp"] <= 0.5:
            return (
                self.status(
                    "waiting_for_observation",
                    "Waiting for current recovery observations",
                )
                if self.episode
                else None
            )
        if self.episode and self.episode["phase"] == "recovery_uncertain":
            # Restart cannot turn an uncertain click into permission to replay.
            # Three fresh living samples can prove revival without any input.
            living = (
                not life.get("dead_candidate", bool(life["status"] & 0x20))
                and not life["ghost_candidate"]
                and not life["status"] & 0x420
                and life["appearance"] == 0
                and life["current_hp"] > 0
            )
            if not living:
                self.healthy_samples = 0
                return self.status(
                    "recovery_blocked",
                    "Recovery input was uncertain; waiting for verified living memory without retrying",
                )
            if life["timestamp"] != self.last_sample:
                self.healthy_samples += 1
                self.last_sample = life["timestamp"]
            if self.healthy_samples < 3:
                return self.status(
                    "verifying_revive",
                    "Reconciling recovery from fresh living observations",
                )
            self.episode["phase"] = "checking_recovery"
            self.save()
        if (
            life.get("dead_candidate", bool(life["status"] & 0x20))
            or life["ghost_candidate"]
        ):
            if self.episode and self.episode["phase"] in (
                "returning_after_revive",
                "returning_with_farmer",
            ):
                self.episode = None  # A new death during return starts a new location.
            if self.episode is None or self.episode["phase"] == "completed":
                self.episode = {
                    "identity": self.identity,
                    "map_id": life["map_id"],
                    "death_position": list(life["position"]),
                    "phase": "waiting_for_revive",
                    "revive_attempts": 0,
                }
                self.pending = None
                self.avoided = set()
                self.save()
            self.healthy_samples = 0
            if life.get("revive_input_supported") is False:
                return self.status(
                    "recovery_blocked",
                    "Automatic Revive is unavailable for this client build; native control qualification failed",
                )
            if self.episode["phase"] == "verifying_revive":
                if self.clock() - self.episode.get("revive_issued", self.clock()) < 8:
                    return self.status(
                        "verifying_revive",
                        "Waiting for the Revive result; no duplicate click",
                    )
            if self.episode.get("revive_attempts", 0) >= 3:
                return self.status(
                    "recovery_blocked", "Revive did not complete after three attempts"
                )
            if not life["revive_ready_candidate"]:
                return self.status(
                    "waiting_for_revive", "Waiting for the game to enable Revive"
                )
            if not focused:
                return self.status(
                    "waiting_for_recovery_focus",
                    "Show / focus Conquer to revive; farming stays On",
                )
            self.episode["phase"] = "revive_submitting"
            self.save()
            if self.issue("revive", None, life, intent["revision"]):
                self.episode["phase"] = "verifying_revive"
                self.episode["revive_issued"] = self.clock()
                self.episode["revive_attempts"] = (
                    self.episode.get("revive_attempts", 0) + 1
                )
                self.save()
            else:
                self.episode["phase"] = "waiting_for_revive"
                self.save()
                return self.status(
                    "waiting_for_revive",
                    "Revive input was held before submission; observing again",
                )
            return self.status(
                "verifying_revive", "Revive pressed; checking health and ghost state"
            )
        if self.episode is None or self.episode["phase"] == "completed":
            return None
        if (
            life["map_id"] != self.episode["map_id"]
            or life["map_id"] != self.terrain.map_id
        ):
            return self.status(
                "recovery_blocked",
                "Revival changed map; a return connection must be mapped",
            )
        if self.episode["phase"] == "returning_with_farmer":
            if (
                max(
                    abs(a - b)
                    for a, b in zip(life["position"], self.episode["death_position"])
                )
                <= 12
            ):
                self.episode["phase"] = "completed"
                self.save()
            return None  # The regular memory farmer owns movement, defense and healing.
        # Absence of the ghost appearance alone does not establish revival.
        if (
            life["status"] & 0x420
            or life["appearance"] != 0
            or life["current_hp"] <= 0
            or (
                not getattr(self, "delegate_return", False)
                and life["current_hp"] < life["max_hp"] * 0.4
            )
        ):
            self.healthy_samples = 0
            return self.status(
                "waiting_for_recovery_health",
                "Waiting for a living character with enough health to return",
            )
        if life["timestamp"] != self.last_sample:
            self.healthy_samples += 1
            self.last_sample = life["timestamp"]
        if self.healthy_samples < 3:
            return self.status(
                "verifying_revive", "Confirming revival across fresh observations"
            )
        if getattr(self, "delegate_return", False):
            self.episode["phase"] = "returning_with_farmer"
            self.pending = None
            self.save()
            return None
        position = tuple(life["position"])
        destination = tuple(self.episode["death_position"])
        if position == destination:
            self.episode["phase"] = "completed"
            self.save()
            self.pending = None
            return self.status(
                "recovered",
                "Back at the saved death location; resuming selected monsters",
            )
        if self.pending:
            target, issued, source = self.pending
            if position == target:
                if (
                    max(abs(a - b) for a, b in zip(source, target)) >= 8
                    and self.clock() - issued < 0.5
                ):
                    return self.status(
                        "returning_after_revive", "Allowing the jump to settle"
                    )
                self.pending = None
            elif self.clock() - issued > 5:
                if len(self.avoided) >= 6:
                    return self.status(
                        "recovery_blocked", "Return path met repeated obstructions"
                    )
                dx, dy = target[0] - source[0], target[1] - source[1]
                blocked = (
                    position[0] + (1 if dx > 0 else -1 if dx < 0 else 0),
                    position[1] + (1 if dy > 0 else -1 if dy < 0 else 0),
                )
                if blocked in self.avoided or not self.terrain.walkable(blocked):
                    return self.status(
                        "recovery_blocked",
                        "Return movement stopped outside the expected path",
                    )
                self.avoided.add(blocked)
                self.episode["avoided_tiles"] = sorted(self.avoided)
                self.pending = None
                self.save()
            else:
                return self.status(
                    "returning_after_revive", "Checking arrival on the return route"
                )
        if not focused:
            return self.status(
                "waiting_for_recovery_focus",
                "Show / focus Conquer to jump back; farming stays On",
            )
        try:
            path = self.terrain.path(position, destination, avoid=self.avoided)
            target = native_waypoint(path)
        except (ValueError, IndexError) as error:
            return self.status("recovery_blocked", str(error))
        movement = (
            "jump" if max(abs(a - b) for a, b in zip(target, position)) >= 8 else "run"
        )
        if self.issue(movement, target, life, intent["revision"]):
            self.pending = (target, self.clock(), position)
            self.episode.update(
                phase="returning_after_revive",
                last_verified_position=list(position),
                next_waypoint=list(target),
            )
            self.save()
        return self.status(
            "returning_after_revive", f"Returning to {destination[0]}, {destination[1]}"
        )


class EmbeddedRecoveryInput:
    def __init__(self, observer, control, *, terrain=None, layout=None):
        self.observer, self.control, self.terrain = observer, control, terrain
        self._layout = layout

    def layout_revision(self, target):
        if self._layout is None or self._layout.target is not target:
            from conquest.layout_revision import SharedLayoutRevision
            from conquest.merchants.memory import GuiReader

            gui = GuiReader.for_session(self.observer.adapter)
            self._layout = SharedLayoutRevision(
                target, windows=gui.windows, gui_size=gui.viewport_size
            )
        return self._layout

    def read_life(self):
        try:
            return self.observer.read_life()
        except ValueError as error:
            if str(error) in (
                "Life state changed during observation",
                "Player pointer changed during life observation",
                "Life observation expired",
                "Health fields or pointer topology changed during sampling",
            ):
                raise CaptureUnavailable(str(error)) from error
            raise

    def __call__(self, kind, destination, observed, revision):
        # Match the bridge's lock order: memory/lifetime before control intent.
        with self.observer.lock:
            return self.control.dispatch_recovery(
                revision, lambda: self.send(kind, destination, observed)
            )

    def send(self, kind, destination, observed):
        from conquest.desktop_runtime import physical_coordinates
        from conquest.foreground import foreground_click

        observer = self.observer
        life = self.read_life()
        if (
            life.map_id != observed["map_id"]
            or tuple(life.position) != tuple(observed["position"])
            or life.ghost_candidate != observed["ghost_candidate"]
        ):
            raise CaptureUnavailable("Character changed before recovery input")
        target = observer.bridge.operations.target
        before = target.snapshot()
        from conquest.viewport import size_for, clear_scene, revive_point

        try:
            viewport = size_for(observer)
        except ValueError as error:
            raise CaptureUnavailable(str(error)) from error
        if tuple(before["client_size"]) != viewport:
            raise CaptureUnavailable("Recovery client geometry changed before input")
        if before["foreground"] != before["root_hwnd"] or before["minimized"]:
            raise CaptureUnavailable("Recovery waiting for game focus; no input sent")
        from conquest.mouse_priority import require_idle

        require_idle()
        observer.focus_client()  # Ctrl must reach the client, not the Tk sidebar.

        def player_anchor(current_life):
            from conquest.scene_input import memory_player_anchor

            try:
                return memory_player_anchor(observer, current_life)
            except ValueError as error:
                if str(error) in (
                    "Player draw position is unavailable or changed",
                    "Player projection changed during observation",
                ):
                    raise CaptureUnavailable(str(error)) from error
                raise

        if kind == "revive":
            if not life.revive_ready_candidate:
                raise CaptureUnavailable("Revive is not ready; reobserve before input")
            try:
                point = revive_point(observer.adapter, viewport)
            except ValueError as error:
                raise CaptureUnavailable(str(error)) from error
        elif kind in ("jump", "run"):
            # Low health must not prevent escaping toward healing supplies.
            # A new death is transient: reobserve and let the owner revive.
            if life.ghost_candidate or life.status & 0x420 or life.current_hp <= 0:
                raise CaptureUnavailable("Life state changed before route movement")
            dx, dy = (
                destination[0] - life.position[0],
                destination[1] - life.position[1],
            )
            if not 1 <= max(abs(dx), abs(dy)) <= 12:
                raise ValueError("Return jump must follow one checked straight segment")
            if dx and dy and abs(dx) + abs(dy) > 4:
                from conquest.navigation import clear_segment, read_terrain

                if self.terrain is None or self.terrain.map_id != life.map_id:
                    self.terrain = read_terrain(
                        installation_path(r"C:\Program Files\Classic Conquer 2.0"),
                        life.map_id,
                    )
                if not clear_segment(self.terrain, life.position, destination):
                    raise ValueError("Return jump crosses blocked terrain")
            if kind == "jump" and max(abs(dx), abs(dy)) < 8:
                raise ValueError("Short return segments must use running")
            anchor = player_anchor(life)
            point = (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
            if not clear_scene(point, viewport):
                raise ValueError("Projected route tile is outside the clear scene")
        else:
            raise ValueError("Unknown recovery action")

        def current_point():
            fresh = self.read_life()
            fresh_viewport = size_for(observer)
            if (
                fresh.map_id != life.map_id
                or tuple(fresh.position) != tuple(life.position)
                or fresh.ghost_candidate != life.ghost_candidate
                or fresh_viewport != viewport
            ):
                raise CaptureUnavailable(
                    "Character or viewport changed before recovery input"
                )
            if kind == "revive":
                if not fresh.revive_ready_candidate:
                    raise CaptureUnavailable(
                        "Revive state changed before recovery input"
                    )
                try:
                    return revive_point(observer.adapter, fresh_viewport)
                except ValueError as error:
                    raise CaptureUnavailable(str(error)) from error
            if fresh.ghost_candidate or fresh.status & 0x420 or fresh.current_hp <= 0:
                raise CaptureUnavailable("Life state changed before route movement")
            fresh_anchor = player_anchor(fresh)
            dx, dy = (
                destination[0] - fresh.position[0],
                destination[1] - fresh.position[1],
            )
            fresh_point = (
                fresh_anchor[0] + (dx - dy) * 32,
                fresh_anchor[1] + (dx + dy) * 16,
            )
            if not clear_scene(fresh_point, fresh_viewport):
                raise CaptureUnavailable("Projected route tile left the clear scene")
            return fresh_point

        with physical_coordinates():
            try:
                layout = self.layout_revision(target)
                revision = layout.qualified()
            except ValueError as error:
                raise CaptureUnavailable(str(error)) from error
            if revision.gui_size != viewport:
                raise CaptureUnavailable(
                    "Recovery viewport changed while qualifying layout"
                )
            size = list(revision.client_size)

            def route_actionability(route_point, layout_state):
                from conquest.target_actionability import require_target_actionable

                windows = [
                    {"name": panel[0], "geometry": panel[2]}
                    for panel in layout_state.panels
                ]
                require_target_actionable(route_point, viewport, size, windows)

            if kind != "revive":
                route_actionability(point, revision)
            physical = [
                round(point[0] * size[0] / viewport[0]),
                round(point[1] * size[1] / viewport[1]),
            ]
            diagnostics = {
                "action": kind,
                "source": list(life.position),
                "destination": destination,
                "issued_at": time.time(),
                "point": point,
            }

            def before_press():
                fresh_point = current_point()
                if fresh_point != point:
                    raise CaptureUnavailable(
                        "Recovery projection changed before button press"
                    )
                from conquest.memory_build_layout import CLIENT_SHA256_1078

                if (
                    kind == "revive"
                    and observer.adapter.expected_sha256 == CLIENT_SHA256_1078
                ):
                    from conquest.native_revive import point as native_revive_point
                    from conquest.merchants.memory import HoverNotReady

                    try:
                        native_revive_point(
                            observer.adapter, viewport, require_hover=True
                        )
                    except (ValueError, HoverNotReady) as error:
                        raise CaptureUnavailable(str(error)) from error
                if kind != "revive":
                    route_actionability(fresh_point, layout.assert_current(revision))

            try:
                diagnostics["input"] = foreground_click(
                    target,
                    *physical,
                    size,
                    control=kind == "jump",
                    require_foreground=True,
                    expected_origin=revision.client_origin,
                    diagnostics=diagnostics,
                    before_press=before_press,
                    layout_guard=lambda: layout.assert_current(revision),
                )
            except Exception as error:
                diagnostics["error"] = str(error)
                raise
            finally:
                with Path(state_path("reports/recovery-input.jsonl")).open(
                    "a", encoding="utf-8"
                ) as report:
                    report.write(json.dumps(diagnostics) + "\n")
