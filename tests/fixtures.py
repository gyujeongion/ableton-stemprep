"""Builds small synthetic Live sets that contain only the elements the tool reads."""

import gzip
import xml.etree.ElementTree as ET

_next = [1000]


def _pid():
    _next[0] += 1
    return str(_next[0])


def _param(parent, tag, value):
    p = ET.SubElement(parent, tag)
    ET.SubElement(p, "LomId", Value="0")
    ET.SubElement(p, "Manual", Value=str(value))
    ET.SubElement(p, "AutomationTarget", Id=_pid())
    return p


def device(tag="Eq8", list_id=0, params=("Gain",)):
    d = ET.Element(tag, Id=str(list_id))
    _param(d, "On", "true")
    for p in params:
        _param(d, p, "1")
    ET.SubElement(d, "UserName", Value="")
    return d


def utility(list_id=0):
    d = device("StereoGain", list_id, params=("Gain", "Balance", "Mute", "Mono", "StereoWidth"))
    d.find("StereoWidth/Manual").set("Value", "0")  # template in a weird state
    return d


def track(kind, tid, name, group="-1", vol=1.0, pan=0.0, on=True, devices=(), clips=(),
          sends=(), out="AudioOut/Main", frozen=False, inputs=()):
    t = ET.Element(kind, Id=str(tid))
    n = ET.SubElement(t, "Name")
    ET.SubElement(n, "EffectiveName", Value=name)
    ET.SubElement(n, "UserName", Value=name)
    ae = ET.SubElement(t, "AutomationEnvelopes")
    ET.SubElement(ae, "Envelopes")
    ET.SubElement(t, "TrackGroupId", Value=str(group))
    ET.SubElement(t, "Freeze", Value="true" if frozen else "false")
    dc = ET.SubElement(t, "DeviceChain")
    ai = ET.SubElement(dc, "AudioInputRouting")
    ET.SubElement(ai, "Target", Value=inputs[0] if inputs else "AudioIn/None")
    ao = ET.SubElement(dc, "AudioOutputRouting")
    ET.SubElement(ao, "Target", Value=out)
    ET.SubElement(ao, "UpperDisplayString", Value=out)
    m = ET.SubElement(dc, "Mixer")
    s = ET.SubElement(m, "Sends")
    for i, amt in enumerate(sends):
        h = ET.SubElement(s, "TrackSendHolder", Id=str(i))
        _param(h, "Send", amt)
    _param(m, "Speaker", "true" if on else "false")
    ET.SubElement(m, "PanMode", Value="0")
    _param(m, "Pan", pan)
    _param(m, "Volume", vol)
    inner = ET.SubElement(dc, "DeviceChain")
    devs = ET.SubElement(inner, "Devices")
    for d in devices:
        devs.append(d)
    seq = ET.SubElement(dc, "MainSequencer")
    ev = ET.SubElement(ET.SubElement(ET.SubElement(seq, "Sample"), "ArrangerAutomation"), "Events")
    for i, (a, b) in enumerate(clips):
        c = ET.SubElement(ev, "AudioClip", Id=str(i), Time=str(a))
        ET.SubElement(c, "CurrentStart", Value=str(a))
        ET.SubElement(c, "CurrentEnd", Value=str(b))
        ET.SubElement(c, "Disabled", Value="false")
    return t


def automate(t, pointee, values=((0, 1.0), (16, 0.5))):
    envs = t.find("AutomationEnvelopes/Envelopes")
    e = ET.SubElement(envs, "AutomationEnvelope", Id=str(len(envs)))
    ET.SubElement(ET.SubElement(e, "EnvelopeTarget"), "PointeeId", Value=pointee)
    evs = ET.SubElement(ET.SubElement(e, "Automation"), "Events")
    for i, (time, v) in enumerate(values):
        ET.SubElement(evs, "FloatEvent", Id=str(i), Time=str(time), Value=str(v))
    return e


def live_set(tracks, returns=0, sends_pre=()):
    root = ET.Element("Ableton", MajorVersion="5", MinorVersion="12.0_12120", Creator="Ableton Live 12.1.1")
    ls = ET.SubElement(root, "LiveSet")
    ET.SubElement(ls, "NextPointeeId", Value=str(_next[0] + 1))
    tr = ET.SubElement(ls, "Tracks")
    for t in tracks:
        tr.append(t)
    for i in range(returns):
        tr.append(track("ReturnTrack", 900 + i, "Return %d" % i))
    mt = ET.SubElement(ls, "MainTrack")
    mm = ET.SubElement(ET.SubElement(mt, "DeviceChain"), "Mixer")
    _param(mm, "TimeSignature", "201")
    sp = ET.SubElement(ls, "SendsPre")
    for i, pre in enumerate(sends_pre):
        ET.SubElement(sp, "SendPreBool", Id=str(i), Value="true" if pre else "false")
    ts = ET.SubElement(ls, "TimeSelection")
    ET.SubElement(ts, "AnchorTime", Value="0")
    ET.SubElement(ts, "OtherTime", Value="0")
    ls.find("NextPointeeId").set("Value", str(_next[0] + 1))
    return root


def write(root, path):
    data = ('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")).encode()
    with gzip.open(path, "wb") as f:
        f.write(data)
