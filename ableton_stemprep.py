#!/usr/bin/env python3
"""ableton-stemprep: prepare an Ableton Live set for stem export.

Reads a .als file (gzip-compressed XML) and writes a NEW .als in which

* group tracks are flattened, with each group's fader gain folded into its
  children and the group's effect chain copied onto every child
  (a Utility keeps the child's level going into the copied effects),
* vocal groups are flattened without copying their effects (raw vocals),
* tracks are optionally renamed from a JSON map,
* the arrangement time selection is set to the song plus 2 bars before and
  4 bars after, ready for File > Export Audio/Video.

Anything that cannot be reproduced exactly is either refused (the group is
left untouched) or reported. The input file is never modified.

Standard library only. Python 3.8+.
"""

import argparse
import copy
import gzip
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

__version__ = "0.1.0"

MAX_FADER = 1.99526231  # +6 dB, the top of Live's track fader
SEND_MIN = 0.0003162277571  # -inf on a send knob
DEFAULT_VOCAL_PATTERN = r"(?i)(\bvox\b|vocal|\bvoc\b|acapella|a cappella|adlib|보컬)"
PRE_GAIN_NAME = "STEM pre-gain"
# Devices that react to the level of the summed signal: a copy on every track
# does not give the same result as one instance on the group bus.
SUMMING_DEVICES = {"Compressor2", "Compressor", "GlueCompressor", "Limiter", "MultibandDynamics", "Gate",
                   "DrumBuss", "Saturator", "Overdrive", "Pedal", "Amp", "Redux", "Redux2", "Erosion",
                   "VinylDistortion", "Roar", "DynamicTube", "Tube"}
# Plugins and Max devices: contents unknown, they may well be dynamics or distortion.
OPAQUE_DEVICES = {"PluginDevice", "AuPluginDevice", "MxDeviceAudioEffect", "MxDeviceMidiEffect",
                  "MxDeviceInstrument"}
# Devices with free-running modulation: separate copies may not stay in phase.
MODULATION_DEVICES = {"Chorus2", "Chorus", "Phaser", "PhaserNew", "Flanger", "AutoPan", "AutoFilter",
                      "FrequencyShifter", "Shifter"}

# Defaults applied to the Utility (StereoGain) that is cloned from the set.
UTILITY_DEFAULTS = {
    "On": "true",
    "PhaseInvertL": "false",
    "PhaseInvertR": "false",
    "ChannelMode": "1",
    "StereoWidth": "1",
    "MidSideBalance": "1",
    "MidSideBalanceOn": "false",
    "Mono": "false",
    "BassMono": "false",
    "BassMonoFrequency": "120",
    "Balance": "0",
    "Mute": "false",
    "DcFilter": "false",
}


class StemPrepError(Exception):
    pass


# --------------------------------------------------------------------------- io


def load_set(path):
    try:
        with gzip.open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise StemPrepError("%s: not a gzip-compressed Live set (%s)" % (path, e))
    root = ET.fromstring(data)
    if root.tag != "Ableton" or root.find("LiveSet") is None:
        raise StemPrepError("%s: not an Ableton Live set" % path)
    return root


def save_set(root, path):
    """Write atomically; the file is read back and checked before it replaces anything."""
    body = ET.tostring(root, encoding="unicode")
    data = ('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n").encode("utf-8")
    tmp = path + ".part"
    with gzip.open(tmp, "wb") as f:
        f.write(data)
    problems = validate(load_set(tmp))
    if problems:
        os.remove(tmp)
        raise StemPrepError("written file failed re-validation, nothing replaced:\n  " + "\n  ".join(problems))
    os.replace(tmp, path)


def live_version(root):
    """'12.1.1' style version from the Creator attribute, or ''."""
    m = re.search(r"(\d+(?:\.\d+)*)", root.get("Creator", ""))
    return m.group(1) if m else ""


# ---------------------------------------------------------------- track helpers


def db(lin):
    lin = float(lin)
    return float("-inf") if lin <= 0 else 20 * math.log10(lin)


def fmt_db(lin):
    v = db(lin)
    return "-inf" if v == float("-inf") else "%+.1f dB" % v


def val(el, path, default=None):
    node = el.find(path)
    return default if node is None else node.get("Value", default)


def set_val(el, path, value):
    node = el.find(path)
    if node is None:
        raise StemPrepError("missing element %s" % path)
    node.set("Value", str(value))


def tracks_el(root):
    return root.find("LiveSet/Tracks")


def all_tracks(root):
    return [t for t in tracks_el(root) if t.tag in ("AudioTrack", "MidiTrack", "GroupTrack")]


def tid(t):
    return t.get("Id")


def name(t):
    return val(t, "Name/EffectiveName", "") or val(t, "Name/UserName", "")


def set_name(t, new):
    set_val(t, "Name/UserName", new)
    set_val(t, "Name/EffectiveName", new)


def group_id(t):
    return val(t, "TrackGroupId", "-1")


def mixer(t):
    return t.find("DeviceChain/Mixer")


def devices_el(t):
    return t.find("DeviceChain/DeviceChain/Devices")


def device_tags(t):
    d = devices_el(t)
    return [x.tag for x in d] if d is not None else []


def volume(t):
    return float(val(mixer(t), "Volume/Manual", "1"))


def set_volume(t, lin):
    set_val(mixer(t), "Volume/Manual", "%.10g" % lin)


def pan(t):
    return float(val(mixer(t), "Pan/Manual", "0"))


def is_on(t):
    return val(mixer(t), "Speaker/Manual", "true") == "true"


def is_solo(t):
    return val(mixer(t), "SoloSink", "false") == "true"


def is_frozen(t):
    return val(t, "Freeze", "false") == "true"


def envelopes_el(t):
    return t.find("AutomationEnvelopes/Envelopes")


def envelope_pointees(t):
    env = envelopes_el(t)
    if env is None:
        return []
    return [e.find("EnvelopeTarget/PointeeId").get("Value") for e in env]


def send_holders(t):
    sends = mixer(t).find("Sends")
    return list(sends) if sends is not None else []


def send_amount(holder):
    return float(val(holder, "Send/Manual", "0"))


def target_id(el):
    t = el.find("AutomationTarget")
    return t.get("Id") if t is not None else None


def mixer_pointees(t):
    """Pointee ids of the mixer parameters (volume, pan, on/off, sends)."""
    m = mixer(t)
    ids = {}
    for key in ("Volume", "Pan", "Speaker", "SplitStereoPanL", "SplitStereoPanR"):
        el = m.find(key)
        if el is not None and target_id(el):
            ids[target_id(el)] = key
    for i, h in enumerate(send_holders(t)):
        s = h.find("Send")
        if s is not None and target_id(s):
            ids[target_id(s)] = "Send %d" % i
    return ids


def output_target(t):
    return val(t, "DeviceChain/AudioOutputRouting/Target", "")


def is_global_id_tag(tag):
    # Elements whose Id is a set-wide "pointee" id (automation, modulation,
    # MIDI mapping). Everything else uses list-local ids.
    return tag.endswith("Target") or tag == "Pointee" or tag.startswith("ControllerTargets.")


def global_ids(root):
    out = []
    for e in root.iter():
        if is_global_id_tag(e.tag) and e.get("Id") is not None:
            out.append(int(e.get("Id")))
    return out


class Ids:
    """Hands out new pointee ids starting at the set's NextPointeeId."""

    def __init__(self, root):
        self.el = root.find("LiveSet/NextPointeeId")
        start = int(self.el.get("Value"))
        existing = global_ids(root)
        self.next = max([start] + [i + 1 for i in existing])

    def reid(self, subtree):
        """Give every global id inside subtree a fresh id. Returns old->new."""
        mapping = {}
        for e in subtree.iter():
            if is_global_id_tag(e.tag) and e.get("Id") is not None:
                mapping[e.get("Id")] = str(self.next)
                e.set("Id", str(self.next))
                self.next += 1
        for p in subtree.iter("PointeeId"):
            if p.get("Value") in mapping:
                p.set("Value", mapping[p.get("Value")])
        return mapping

    def commit(self):
        self.el.set("Value", str(self.next))


# ------------------------------------------------------------------ structure


class SetModel:
    def __init__(self, root, vocal_pattern=DEFAULT_VOCAL_PATTERN, vocal=(), not_vocal=()):
        self.root = root
        self.vocal_re = re.compile(vocal_pattern) if vocal_pattern else None
        self.vocal_specs = list(vocal)
        self.not_vocal_specs = list(not_vocal)
        self.refresh()
        # decided once, before any group is removed
        self.vocal_ids = {tid(t) for t in self.tracks if self._vocal_by_structure(t)}

    def refresh(self):
        self.tracks = all_tracks(self.root)
        self.by_id = {tid(t): t for t in self.tracks}

    def parent(self, t):
        return self.by_id.get(group_id(t))

    def ancestors(self, t):
        out, p = [], self.parent(t)
        while p is not None:
            out.append(p)
            p = self.parent(p)
        return out

    def children(self, g):
        return [t for t in self.tracks if group_id(t) == tid(g)]

    def depth(self, t):
        return len(self.ancestors(t))

    def groups(self):
        return [t for t in self.tracks if t.tag == "GroupTrack"]

    def own_vocal(self, t):
        if matches(t, self.not_vocal_specs):
            return False
        if matches(t, self.vocal_specs):
            return True
        return bool(self.vocal_re and self.vocal_re.search(name(t)))

    def _vocal_by_structure(self, t):
        """A track is vocal if it or any enclosing group is."""
        if matches(t, self.not_vocal_specs):
            return False
        return any(self.own_vocal(x) for x in [t] + self.ancestors(t))

    def is_vocal(self, t):
        return tid(t) in self.vocal_ids

    def audible(self, t):
        return all(is_on(x) for x in [t] + self.ancestors(t))

    def source_references(self, t, with_tap=False):
        """Other tracks/devices that take audio from this track.

        with_tap=True returns (track name, tap point) pairs, where the tap is
        PreFxOut, PostFxOut or TrackOut (after the mixer)."""
        if not hasattr(self, "_refs"):
            # one pass over the set; routings do not change while flattening
            self._refs = {}
            for other in self.tracks + list(tracks_el(self.root).iter("ReturnTrack")):
                for e in other.iter():
                    v = e.get("Value")
                    if v and v.startswith("AudioIn/Track."):
                        src, _, tap = v[len("AudioIn/Track."):].partition("/")
                        self._refs.setdefault(src, set()).add((tid(other), name(other), tap))
        refs = {(n, tap) for owner, n, tap in self._refs.get(tid(t), ()) if owner != tid(t)}
        return sorted(refs) if with_tap else sorted({n for n, _ in refs})


def matches(t, specs):
    for s in specs:
        if s.startswith("#"):
            if tid(t) == s[1:]:
                return True
        elif name(t).casefold() == s.casefold():
            return True
    return False


def sends_pre(root):
    """Per return track: True if its sends are pre-fader."""
    out = []
    sp = root.find("LiveSet/SendsPre")
    if sp is not None:
        for b in sp:
            out.append(b.get("Value") == "true")
    return out


# ----------------------------------------------------------------- utility


def find_utility_template(root):
    for e in root.iter("StereoGain"):
        return e
    return None


def make_utility(template, gain_lin, ids):
    dev = copy.deepcopy(template)
    for key, value in UTILITY_DEFAULTS.items():
        el = dev.find(key)
        if el is None:
            continue
        if el.find("Manual") is not None:
            el.find("Manual").set("Value", value)
        else:
            el.set("Value", value)
    dev.find("Gain/Manual").set("Value", "%.10g" % gain_lin)
    if dev.find("UserName") is not None:
        dev.find("UserName").set("Value", PRE_GAIN_NAME)
    ids.reid(dev)
    return dev


# ----------------------------------------------------------------- flatten


def check_group(model, g):
    """Reasons a group cannot be flattened exactly. Empty list = OK."""
    reasons = []
    refs = model.source_references(g)
    if refs:
        reasons.append("used as an audio source by: %s" % ", ".join(sorted(set(refs))))
    automated = set(envelope_pointees(g)) & set(mixer_pointees(g))
    if automated:
        what = sorted(set(mixer_pointees(g)[p] for p in automated))
        reasons.append("group mixer is automated (%s)" % ", ".join(what))
    if abs(pan(g)) > 1e-6:
        reasons.append("group pan is not centred")
    if val(mixer(g), "PanMode", "0") != "0":
        reasons.append("group uses split stereo panning")
    if float(val(g, "TrackDelay/Value", "0")) != 0:
        for k in model.children(g):
            if val(k, "TrackDelay/IsValueSampleBased") != val(g, "TrackDelay/IsValueSampleBased"):
                reasons.append("group track delay and %s's delay use different units (ms/samples)" % name(k))
                break
    active = [i for i, h in enumerate(send_holders(g)) if send_amount(h) > SEND_MIN * 1.01]
    if active:
        reasons.append("group has active sends (%s)" % ", ".join("#%d" % (i + 1) for i in active))
    return reasons


def flatten_group(model, g, ids, utility_template, copy_fx, report):
    """Flatten one group (its inner groups must already be handled)."""
    g_vol = volume(g)
    g_on = is_on(g)
    g_devices = list(devices_el(g)) if devices_el(g) is not None else []
    do_fx = copy_fx and bool(g_devices)
    g_env = envelopes_el(g)
    pre = sends_pre(model.root)
    entry = {
        "group": name(g),
        "id": tid(g),
        "group_gain": fmt_db(g_vol),
        "vocal": model.is_vocal(g),
        "group_fx": [d.tag for d in g_devices],
        "fx_copied": do_fx,
        "children": [],
        "warnings": [],
    }
    if g_devices and not do_fx:
        entry["warnings"].append(
            "group effects NOT copied (%s): %s" % (
                "vocal group, kept raw" if entry["vocal"] else "--no-fx",
                ", ".join(entry["group_fx"])))
    summing = sorted({d.tag for d in g_devices if d.tag in SUMMING_DEVICES})
    opaque = sorted({d.tag for d in g_devices if d.tag in OPAQUE_DEVICES})
    if do_fx and summing:
        entry["warnings"].append(
            "%s react to the summed signal: the stems will not add up to the group bus (audible difference); "
            "use --keep-nonlinear to export this group as one stem instead" % ", ".join(summing))
    if do_fx and opaque:
        entry["warnings"].append(
            "plugin/Max devices in the group chain (%d) cannot be checked; if they are dynamics or "
            "distortion the stems will not add up to the group bus" % sum(d.tag in OPAQUE_DEVICES for d in g_devices))
    moving = sorted({d.tag for d in g_devices if d.tag in MODULATION_DEVICES})
    if do_fx and moving:
        entry["warnings"].append(
            "copied modulation effects (%s) run separately on each track; unless they are tempo-synced "
            "their LFOs may not line up, and the summed stems can differ from the bus" % ", ".join(moving))
    kids = model.children(g)
    plans = [plan_child(k, g_vol, do_fx, utility_template) for k in kids]
    # check everything before changing anything
    if do_fx:
        frozen = [name(k) for k in kids if is_frozen(k)]
        if frozen:
            raise StemPrepError("frozen children cannot take effects: %s" % ", ".join(frozen))
    for k, (_, new_vol, _) in zip(kids, plans):
        if new_vol > MAX_FADER * 1.0001:
            raise StemPrepError("%s would need %s on its fader (max +6 dB)" % (name(k), fmt_db(new_vol)))

    g_delay = float(val(g, "TrackDelay/Value", "0"))
    for k, (utility_used, new_vol, vol_automated) in zip(kids, plans):
        c = {"track": name(k), "id": tid(k)}
        k_vol = volume(k)
        if output_target(k) != "AudioOut/GroupTrack":
            # does not pass through the group: only leave the group
            set_val(k, "TrackGroupId", group_id(g))
            c["volume"] = "%s -> %s" % (fmt_db(k_vol), fmt_db(k_vol))
            c["note"] = "routed to %s, not through the group: unchanged" % (output_target(k) or "?")
            entry["children"].append(c)
            continue
        if g_delay:
            set_val(k, "TrackDelay/Value", "%.10g" % (float(val(k, "TrackDelay/Value", "0")) + g_delay))

        # 1. move into the parent of the group
        set_val(k, "TrackGroupId", group_id(g))
        if output_target(k) == "AudioOut/GroupTrack":
            old = k.find("DeviceChain/AudioOutputRouting")
            parent_dc = k.find("DeviceChain")
            idx = list(parent_dc).index(old)
            parent_dc.remove(old)
            parent_dc.insert(idx, copy.deepcopy(g.find("DeviceChain/AudioOutputRouting")))

        # 2. gain (and effects)
        if do_fx:
            chain = devices_el(k)
            if utility_used:
                util = make_utility(utility_template, k_vol, ids)
                append_device(chain, util)
                if vol_automated:
                    move_volume_automation(k, target_id(util.find("Gain")))
                    c["automation"] = "fader automation moved to the pre-gain Utility"
            for d in g_devices:
                dup = copy.deepcopy(d)
                mapping = ids.reid(dup)
                append_device(chain, dup)
                if g_env is not None:
                    for env in g_env:
                        p = env.find("EnvelopeTarget/PointeeId").get("Value")
                        if p in mapping:
                            add_envelope(k, env, mapping[p])
            c["fx_added"] = [d.tag for d in g_devices]
        if utility_used:
            c["pre_gain"] = fmt_db(k_vol)
        elif do_fx and k_vol != 1.0:
            entry["warnings"].append(
                "%s: level into the copied effects differs by %s (no Utility in the set to copy)" % (
                    name(k), fmt_db(1 / k_vol)))
        set_volume(k, new_vol)
        if vol_automated and not utility_used and abs(g_vol - 1.0) > 1e-9:
            clipped = scale_volume_automation(k, g_vol)
            if clipped:
                entry["warnings"].append("%s: volume automation clipped at +6 dB" % name(k))
        c["volume"] = "%s -> %s" % (fmt_db(k_vol), fmt_db(new_vol))

        # 3. sends: post-fader sends now sit after the group gain (and effects)
        # post-fader tap: the child level (fader or pre-gain Utility) is unchanged,
        # so only the group fader gain is added in front of it
        factor = g_vol if g_vol > 0 else 1.0
        for i, h in enumerate(send_holders(k)):
            amt = send_amount(h)
            if amt <= SEND_MIN * 1.01:
                continue
            is_pre = i < len(pre) and pre[i]
            if do_fx:
                entry["warnings"].append(
                    "%s: send #%d now taps the signal after the copied group effects" % (name(k), i + 1))
            # level change at the tap point: post-fader sees the fader change,
            # pre-fader sees the pre-gain Utility (if any)
            tap_factor = (k_vol if utility_used else 1.0) if is_pre else factor
            if abs(tap_factor - 1.0) < 1e-9:
                continue
            new_amt = amt / tap_factor
            if new_amt > 1.0:
                new_amt = 1.0
                entry["warnings"].append("%s: send #%d clipped at 0 dB" % (name(k), i + 1))
            set_val(h, "Send/Manual", "%.10g" % new_amt)
        if do_fx and abs(pan(k)) > 1e-6:
            entry["warnings"].append("%s: its pan now comes after the copied group effects" % name(k))

        for other, tap in model.source_references(k, with_tap=True):
            changed = []
            if do_fx and tap != "PreFxOut":
                changed.append("effects")
            if abs(new_vol - k_vol) > 1e-9 and tap not in ("PreFxOut", "PostFxOut"):
                changed.append("gain")
            if changed:
                entry["warnings"].append(
                    "%s feeds %s (%s); that signal now includes the group %s" % (
                        name(k), other, tap, " and ".join(changed)))

        # a soloed group makes all of its children audible: carry the solo down
        if is_solo(g) and not is_solo(k):
            mixer(k).find("SoloSink").set("Value", "true")
            c["solo"] = "group was soloed"

        # 4. group off -> children off
        if not g_on and is_on(k):
            set_val(mixer(k), "Speaker/Manual", "false")
            c["muted"] = "group was deactivated"
        entry["children"].append(c)

    tracks_el(model.root).remove(g)
    model.refresh()
    report.append(entry)


def plan_child(k, g_vol, do_fx, utility_template):
    """(use a pre-gain Utility?, new fader value, is the fader automated?)"""
    k_vol = volume(k)
    mp = mixer_pointees(k)
    vol_automated = any(mp[p] == "Volume" for p in set(envelope_pointees(k)) & set(mp))
    if output_target(k) != "AudioOut/GroupTrack":
        return False, k_vol, vol_automated
    if do_fx and vol_automated and utility_template is None:
        raise StemPrepError("%s has volume automation and there is no Utility in the set to carry it "
                            "in front of the copied effects" % name(k))
    use_utility = do_fx and (k_vol != 1.0 or vol_automated) and utility_template is not None
    return use_utility, (g_vol if use_utility else k_vol * g_vol), vol_automated


def append_device(chain, dev):
    """Append a device with a list id that is unique inside the chain."""
    used = [int(d.get("Id")) for d in chain if d.get("Id") is not None]
    dev.set("Id", str(max(used) + 1 if used else 0))
    chain.append(dev)


def add_envelope(track, env, new_pointee):
    envs = envelopes_el(track)
    used = [int(e.get("Id")) for e in envs]
    dup = copy.deepcopy(env)
    dup.set("Id", str(max(used) + 1 if used else 0))
    dup.find("EnvelopeTarget/PointeeId").set("Value", new_pointee)
    envs.append(dup)


def move_volume_automation(track, new_pointee):
    vol_id = target_id(mixer(track).find("Volume"))
    for env in envelopes_el(track):
        p = env.find("EnvelopeTarget/PointeeId")
        if p.get("Value") == vol_id:
            p.set("Value", new_pointee)


def scale_volume_automation(track, factor):
    vol_id = target_id(mixer(track).find("Volume"))
    clipped = False
    for env in envelopes_el(track):
        if env.find("EnvelopeTarget/PointeeId").get("Value") != vol_id:
            continue
        for ev in env.iter("FloatEvent"):
            v = float(ev.get("Value")) * factor
            if v > MAX_FADER:
                v, clipped = MAX_FADER, True
            ev.set("Value", "%.10g" % v)
    return clipped


def strip_track_fx(track):
    """Remove every device of an audio track (raw vocals)."""
    chain = devices_el(track)
    if chain is None or not len(chain):
        return []
    removed_ids = set()
    tags = []
    for d in list(chain):
        tags.append(d.tag)
        for e in d.iter():
            if is_global_id_tag(e.tag) and e.get("Id"):
                removed_ids.add(e.get("Id"))
        chain.remove(d)
    envs = envelopes_el(track)
    for env in list(envs):
        if env.find("EnvelopeTarget/PointeeId").get("Value") in removed_ids:
            envs.remove(env)
    return tags


# ------------------------------------------------------------------- range


def bar_length(root):
    v = int(float(val(root, "LiveSet/MainTrack/DeviceChain/Mixer/TimeSignature/Manual", "201")))
    num, den = v % 99 + 1, 2 ** (v // 99)
    return num * 4.0 / den, "%d/%d" % (num, den)


def song_extent(model, exclude=()):
    start, end = None, None
    for t in model.tracks:
        if t.tag == "GroupTrack" or not model.audible(t) or matches(t, exclude):
            continue
        seq = t.find("DeviceChain/MainSequencer")
        if seq is None:
            continue
        for events in seq.iter("Events"):
            for clip in events:
                if clip.tag not in ("AudioClip", "MidiClip"):
                    continue
                if val(clip, "Disabled", "false") == "true":
                    continue
                s, e = float(val(clip, "CurrentStart")), float(val(clip, "CurrentEnd"))
                start = s if start is None else min(start, s)
                end = e if end is None else max(end, e)
    return start, end


def set_time_selection(root, start, end):
    ts = root.find("LiveSet/TimeSelection")
    if ts is None:
        raise StemPrepError("this set has no TimeSelection element")
    ts.find("AnchorTime").set("Value", "%.10g" % start)
    ts.find("OtherTime").set("Value", "%.10g" % end)


def plan_range(model, spec, exclude, pre_bars=2, post_bars=4):
    bar, sig = bar_length(model.root)
    if spec == "auto":
        s, e = song_extent(model, exclude)
        if s is None:
            return None, "no audible arrangement clips; range not set"
        s_bar = math.floor(s / bar + 1e-9) * bar
        e_bar = math.ceil(e / bar - 1e-9) * bar
        start = s_bar - pre_bars * bar
        note = ""
        if start < 0:
            start = 0.0
            note = " (song starts at bar 1: no room for %d bars before it)" % pre_bars
        end = e_bar + post_bars * bar
        info = "bar %g to bar %g in %s%s" % (start / bar + 1, end / bar + 1, sig, note)
        return (start, end), info
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", spec)
    if not m:
        raise StemPrepError("--range must be auto, none or FROM_BAR:TO_BAR (e.g. 1:97)")
    a, b = float(m.group(1)), float(m.group(2))
    if b <= a or a < 1:
        raise StemPrepError("--range: TO_BAR must be after FROM_BAR, bars start at 1")
    return ((a - 1) * bar, (b - 1) * bar), "bar %g to bar %g in %s" % (a, b, sig)


# --------------------------------------------------------------- validate


def validate(root):
    problems = []
    tracks = all_tracks(root)
    ids = {tid(t) for t in tracks}
    groups = {tid(t) for t in tracks if t.tag == "GroupTrack"}
    for t in tracks:
        g = group_id(t)
        if g != "-1" and g not in groups:
            problems.append("%s points to missing group %s" % (name(t), g))
        if g == "-1" and output_target(t) == "AudioOut/GroupTrack":
            problems.append("%s routes to a group but is not in one" % name(t))
    if len(ids) != len(tracks):
        problems.append("duplicate track ids")
    gids = global_ids(root)
    if len(gids) != len(set(gids)):
        problems.append("duplicate automation ids")
    nxt = int(root.find("LiveSet/NextPointeeId").get("Value"))
    if gids and max(gids) >= nxt:
        problems.append("NextPointeeId is not above the highest id")
    for parent in root.iter():
        local = [c.get("Id") for c in parent if c.get("Id") is not None and not is_global_id_tag(c.tag)]
        if len(local) != len(set(local)):
            problems.append("non-unique list ids inside <%s>" % parent.tag)
    known = set(str(i) for i in gids)
    for t in tracks:
        for p in envelope_pointees(t):
            if p not in known:
                problems.append("%s has automation for a missing parameter (%s)" % (name(t), p))
    return problems


# ------------------------------------------------------------------- scan


def scan(model):
    rows = []
    for t in model.tracks:
        devs = device_tags(t)
        flags = []
        if t.tag == "GroupTrack":
            flags += check_group(model, t)
        if model.is_vocal(t):
            flags.append("vocal")
        if not is_on(t):
            flags.append("off")
        if is_solo(t):
            flags.append("solo")
        if is_frozen(t):
            flags.append("frozen")
        rows.append({
            "id": tid(t),
            "kind": {"AudioTrack": "audio", "MidiTrack": "midi", "GroupTrack": "group"}[t.tag],
            "name": name(t),
            "depth": model.depth(t),
            "volume": fmt_db(volume(t)),
            "devices": devs,
            "flags": flags,
        })
    return rows


def print_scan(rows):
    for r in rows:
        ind = "  " * r["depth"]
        kind = "[G]" if r["kind"] == "group" else ("[M]" if r["kind"] == "midi" else "[A]")
        dev = (" | %d fx" % len(r["devices"])) if r["devices"] else ""
        flags = ("  <- " + "; ".join(r["flags"])) if r["flags"] else ""
        print("%s%s %s  %s%s%s" % (ind, kind, r["name"], r["volume"], dev, flags))


# -------------------------------------------------------------------- run


def check_version(root):
    v = live_version(root)
    major = int(v.split(".")[0]) if v else 0
    if major and major < 11:
        raise StemPrepError("Live %s set: only Live 11 and 12 sets are supported (open and re-save it in Live 12)" % v)
    return v


def run(args):
    root = load_set(args.als)
    check_version(root)
    model = SetModel(root, args.vocal_pattern, args.vocal, args.not_vocal)
    ids = Ids(root)
    utility = find_utility_template(root)
    report = {"input": args.als, "live": live_version(root), "groups": [], "kept": [],
              "vocal_fx_stripped": [], "vocal_tracks_with_fx": [], "renamed": [],
              "range": None, "warnings": []}

    # groups to flatten, innermost first
    targets = []
    for g in model.groups():
        if args.only and not matches(g, args.only):
            continue
        if matches(g, args.keep):
            report["kept"].append({"group": name(g), "reason": "--keep"})
            continue
        targets.append(g)
    targets.sort(key=model.depth, reverse=True)

    for g in targets:
        reasons = check_group(model, g)
        if reasons:
            report["kept"].append({"group": name(g), "reason": "; ".join(reasons)})
            continue
        copy_fx = not args.no_fx and (args.copy_vocal_fx or not model.is_vocal(g))
        if copy_fx and args.keep_nonlinear and (devices_el(g) is not None):
            hit = sorted({d.tag for d in devices_el(g) if d.tag in SUMMING_DEVICES})
            if hit:
                report["kept"].append({"group": name(g), "reason": "--keep-nonlinear: %s" % ", ".join(hit)})
                continue
        try:
            flatten_group(model, g, ids, utility, copy_fx, report["groups"])
        except StemPrepError as e:  # raised before the group is changed
            report["kept"].append({"group": name(g), "reason": str(e)})

    # raw vocals
    for t in model.tracks:
        if t.tag != "AudioTrack" or not model.is_vocal(t):
            continue
        if args.strip_vocal_fx and device_tags(t):
            if is_frozen(t):
                report["warnings"].append("%s is frozen; effects left in place" % name(t))
                continue
            report["vocal_fx_stripped"].append({"track": name(t), "removed": strip_track_fx(t)})
        elif device_tags(t):
            report["vocal_tracks_with_fx"].append({"track": name(t), "devices": device_tags(t)})

    # rename
    if args.rename:
        with open(args.rename, encoding="utf-8") as f:
            mapping = json.load(f)
        for key, new in mapping.items():
            hits = [t for t in model.tracks if matches(t, [key])]
            if len(hits) != 1:
                report["warnings"].append(
                    "rename %r: %s" % (key, "no such track" if not hits else
                                       "%d tracks have this name, use #id" % len(hits)))
                continue
            report["renamed"].append({"from": name(hits[0]), "to": new})
            set_name(hits[0], new)

    # export range
    if args.range != "none":
        rng, info = plan_range(model, args.range, args.range_exclude)
        if rng:
            set_time_selection(root, *rng)
            report["range"] = info
        else:
            report["warnings"].append(info)

    soloed = [name(t) for t in model.tracks if is_solo(t)]
    if soloed:
        report["warnings"].append(
            "solo is active on: %s. Only soloed tracks are heard (and exported as audible in a mix bounce); "
            "clear the solos in Live before exporting if that is not intended" % ", ".join(soloed))
    names = [name(t) for t in model.tracks if t.tag != "GroupTrack"]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        report["warnings"].append("tracks sharing a name (stem files will be ambiguous): " + ", ".join(dups))

    ids.commit()
    problems = validate(root)
    if problems:
        raise StemPrepError("result failed validation, nothing written:\n  " + "\n  ".join(problems))

    out = args.output or default_output(args.als)
    report["output"] = out
    if os.path.realpath(out) == os.path.realpath(args.als):
        raise StemPrepError("refusing to overwrite the input set")
    if not args.dry_run:
        if os.path.exists(out) and not args.force:
            raise StemPrepError("%s exists (use --force to replace it)" % out)
        save_set(root, out)
    report["written"] = not args.dry_run
    return report


def default_output(path):
    base, ext = os.path.splitext(path)
    return base + " STEM" + (ext or ".als")


def print_report(r):
    print("Live set : %s (Live %s)" % (r["input"], r["live"] or "?"))
    print("Output   : %s%s" % (r["output"], "" if r["written"] else "  (dry run, not written)"))
    print()
    print("Flattened groups: %d" % len(r["groups"]))
    for g in r["groups"]:
        fx = (" + effects copied: " + ", ".join(g["group_fx"])) if g["fx_copied"] else ""
        print("  %s (%s)%s%s" % (g["group"], g["group_gain"], " [vocal]" if g["vocal"] else "", fx))
        same = 0
        for c in g["children"]:
            a, b = c["volume"].split(" -> ")
            if a == b and not ({"pre_gain", "muted", "automation", "note", "solo"} & set(c)):
                same += 1
                continue
            extra = (", pre-gain Utility %s" % c["pre_gain"]) if "pre_gain" in c else ""
            extra += (", muted (%s)" % c["muted"]) if "muted" in c else ""
            extra += (", %s" % c["automation"]) if "automation" in c else ""
            extra += (", solo (%s)" % c["solo"]) if "solo" in c else ""
            extra += ("  [%s]" % c["note"]) if "note" in c else ""
            print("    - %s: %s%s" % (c["track"], c["volume"], extra))
        if same:
            print("    - %d track(s) moved out unchanged" % same)
        for w in g["warnings"]:
            print("    ! %s" % w)
    if r["kept"]:
        print("\nGroups left as they are: %d" % len(r["kept"]))
        for k in r["kept"]:
            print("  %s: %s" % (k["group"], k["reason"]))
    if r["vocal_fx_stripped"]:
        print("\nVocal effects removed:")
        for v in r["vocal_fx_stripped"]:
            print("  %s: %s" % (v["track"], ", ".join(v["removed"])))
    if r["vocal_tracks_with_fx"]:
        print("\nVocal tracks that still have effects (not raw; --strip-vocal-fx removes them):")
        for v in r["vocal_tracks_with_fx"]:
            print("  %s: %s" % (v["track"], ", ".join(v["devices"])))
    if r["renamed"]:
        print("\nRenamed:")
        for x in r["renamed"]:
            print("  %s -> %s" % (x["from"], x["to"]))
    print("\nExport range: %s" % (r["range"] or "not set"))
    if r["warnings"]:
        print("\nWarnings:")
        for w in r["warnings"]:
            print("  ! %s" % w)
    print("\nNot done (by design): export, render, saving over the original set.")


# -------------------------------------------------------------------- cli


def build_parser():
    p = argparse.ArgumentParser(
        prog="ableton-stemprep",
        description="Prepare an Ableton Live set for stem export. Writes a new .als, never touches the original.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("als", help="the .als file")
        sp.add_argument("--vocal-pattern", default=DEFAULT_VOCAL_PATTERN,
                        help="regex for vocal track/group names (default: vox, vocal, voc, acapella, adlib)")
        sp.add_argument("--vocal", action="append", default=[], metavar="NAME",
                        help="treat this track/group as vocal (name or #id, repeatable)")
        sp.add_argument("--not-vocal", action="append", default=[], metavar="NAME",
                        help="never treat this track/group as vocal")
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    sc = sub.add_parser("scan", help="show the track tree and what would block flattening")
    common(sc)

    r = sub.add_parser("run", help="write a stem-ready copy of the set")
    common(r)
    r.add_argument("-o", "--output", help="output .als (default: '<name> STEM.als' next to the input)")
    r.add_argument("--keep", action="append", default=[], metavar="NAME",
                   help="leave this group as it is (repeatable)")
    r.add_argument("--only", action="append", default=[], metavar="NAME",
                   help="flatten only these groups (repeatable)")
    r.add_argument("--no-fx", action="store_true", help="never copy group effects (gain only)")
    r.add_argument("--keep-nonlinear", action="store_true",
                   help="leave groups with compressors, limiters, saturators etc. as they are (one stem for the group)")
    r.add_argument("--copy-vocal-fx", action="store_true", help="copy effects of vocal groups too")
    r.add_argument("--strip-vocal-fx", action="store_true",
                   help="remove all devices from vocal audio tracks (raw vocals)")
    r.add_argument("--rename", metavar="MAP.json", help='{"old name or #id": "new name", ...}')
    r.add_argument("--range", default="auto",
                   help="export time selection: auto (song -2 bars / +4 bars), none, or FROM:TO in bars")
    r.add_argument("--range-exclude", action="append", default=[], metavar="NAME",
                   help="ignore this track when finding the song range (reference, guide)")
    r.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    r.add_argument("--force", action="store_true", help="replace an existing output file")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "scan":
            root = load_set(args.als)
            model = SetModel(root, args.vocal_pattern, args.vocal, args.not_vocal)
            rows = scan(model)
            if args.json:
                print(json.dumps({"live": live_version(root), "tracks": rows}, ensure_ascii=False, indent=2))
            else:
                print("Live %s, %d tracks" % (live_version(root) or "?", len(rows)))
                print_scan(rows)
            return 0
        report = run(args)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_report(report)
        return 0
    except StemPrepError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
