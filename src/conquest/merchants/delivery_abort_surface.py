"""Receipt-bound presentation for the close-only abort; no accept fallback."""

from conquest.merchants import delivery_abort_probe as abort


def binding(ui, character, capability, expected=None):
    coordinator = ui.coordinator
    if (
        capability is None
        or coordinator._probe_abort_capability is not capability
        or coordinator.thread != capability[0]
        or coordinator.purpose != abort.PURPOSE
        or str(coordinator.owner) != str(character)
    ):
        raise ValueError("The exact abort worker no longer owns presentation")
    targets = capability[1]()  # Delegated UI presentation only, not gameplay input.
    saved = abort.read()
    state = abort.probe.read_probe()
    if (
        state.get("phase") != "offer_verified"
        or abort.digest(state) != saved["probe_digest"]
        or saved["phase"] not in ("abort_prepared", "cancel_submitted")
        or state["character"] != str(character)
        or state["target_profile_id"] not in targets
    ):
        raise ValueError("Abort presentation receipt changed")
    merchant = state["intent"]["merchant"]
    profile_id = state["target_profile_id"]
    from conquest.character_context import registry, ProfileName

    profiles = registry()
    if profiles:
        resolved = profiles.resolve(profile_id, role="Merchant", server="America")
        if (
            resolved.id != profile_id
            or not resolved.local_enabled
            or resolved.name != str(character)
            or resolved.server != merchant["server"]
        ):
            raise ValueError("Abort merchant profile changed")
        profile = ProfileName(resolved.name, resolved.id)
    else:
        if profile_id != str(character):
            raise ValueError("Abort merchant profile changed")
        profile = character
    identity = merchant["identity"]
    observer = ui.runtime.observers.get(profile)
    controller = ui.runtime.controllers.get(profile)
    if (
        merchant["character"] != str(character)
        or merchant["server"] != "America"
        or observer is None
        or controller is None
        or observer.adapter.identity != identity
        or controller.driver.observer is not observer
    ):
        raise ValueError("Abort observer/controller identity changed")
    observer.adapter.assert_identity()
    target = observer.operations.target
    if (
        type(getattr(target, "hwnd", None)) is not int
        or target.hwnd <= 0
        or controller.driver.target is not target
        or expected is not None
        and (target.hwnd, identity, profile_id) != expected
    ):
        raise ValueError("Abort target window changed")
    host = ui.hosts.get(profile)
    # An active open trade must keep its already qualified owned host. Never
    # detach/reconnect/reparent an unknown replacement while aborting an offer.
    if (
        host is None
        or not host.saved
        or host.mode != "owned"
        or host.saved.hwnd != target.hwnd
        or host.saved.identity != identity
    ):
        raise ValueError("Abort requires the exact already-owned merchant host")
    host.api.assert_owner(target.hwnd, identity)
    return target, host, identity, profile, profile_id


def prepare(ui, character, capability):
    target, host, identity, profile, profile_id = binding(ui, character, capability)
    expected = (target.hwnd, identity, profile_id)
    ui.notebook.select(ui.frames[profile])
    ui.detail_tabs[profile].select(0)
    layout = getattr(ui, "apply_client_compact_layout", None)
    if layout:
        layout()
    ui.root.update_idletasks()
    target, host, identity, profile, profile_id = binding(
        ui, character, capability, expected
    )
    pane = ui.client_panes[profile]
    from conquest.client_attachment import require_viewport

    require_viewport(pane.winfo_width(), pane.winfo_height())
    binding(ui, character, capability, expected)
    siblings = [
        other for other in ui.hosts.values() if other is not host and other.saved
    ]
    for other in siblings:
        other.api.assert_owner(other.saved.hwnd, other.saved.identity)
    binding(ui, character, capability, expected)
    for other in siblings:
        binding(ui, character, capability, expected)
        other.api.show_async(other.saved.hwnd, 0)
    binding(ui, character, capability, expected)
    host.resize(pane.winfo_width(), pane.winfo_height())
    binding(ui, character, capability, expected)
    return profile
