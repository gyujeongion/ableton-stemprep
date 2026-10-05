import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import ableton_stemprep as sp  # noqa: E402
import fixtures as fx  # noqa: E402


def lin(db):
    return 10 ** (db / 20.0)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "Song.als")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, tracks, **kw):
        fx.write(fx.live_set(tracks, **kw), self.src)

    def run_tool(self, *extra):
        args = sp.build_parser().parse_args(["run", self.src] + list(extra))
        report = sp.run(args)
        return report, sp.load_set(report["output"])

    def track(self, root, name):
        hits = [t for t in sp.all_tracks(root) if sp.name(t) == name]
        self.assertEqual(len(hits), 1, name)
        return hits[0]

    def assertDb(self, t, expected):
        self.assertAlmostEqual(sp.db(sp.volume(t)), expected, places=4)


class FlattenGain(Base):
    def test_static_group_gain_is_added_in_db(self):
        self.build([
            fx.track("GroupTrack", 1, "keys", vol=lin(-7)),
            fx.track("AudioTrack", 2, "keys a", group=1, vol=lin(-3), out="AudioOut/GroupTrack"),
            fx.track("MidiTrack", 3, "keys b", group=1, vol=lin(-8), out="AudioOut/GroupTrack"),
        ])
        report, out = self.run_tool("--range", "none")
        self.assertEqual([sp.name(t) for t in sp.all_tracks(out)], ["keys a", "keys b"])
        self.assertDb(self.track(out, "keys a"), -10)
        self.assertDb(self.track(out, "keys b"), -15)
        for t in sp.all_tracks(out):
            self.assertEqual(sp.group_id(t), "-1")
            self.assertEqual(sp.output_target(t), "AudioOut/Main")
        self.assertEqual(sp.validate(out), [])

    def test_nested_groups_apply_each_gain_once(self):
        self.build([
            fx.track("GroupTrack", 1, "outer", vol=lin(-2)),
            fx.track("GroupTrack", 2, "inner", group=1, vol=lin(-3), out="AudioOut/GroupTrack"),
            fx.track("AudioTrack", 3, "x", group=2, vol=lin(-1), out="AudioOut/GroupTrack"),
        ])
        _, out = self.run_tool("--range", "none")
        self.assertDb(self.track(out, "x"), -6)

    def test_child_not_routed_through_group_is_left_alone(self):
        self.build([
            fx.track("GroupTrack", 1, "g", vol=lin(-6), devices=[fx.device("Saturator")]),
            fx.track("AudioTrack", 2, "direct", group=1, vol=lin(-3), out="AudioOut/Main"),
        ])
        _, out = self.run_tool("--range", "none")
        t = self.track(out, "direct")
        self.assertDb(t, -3)
        self.assertEqual(sp.device_tags(t), [])

    def test_post_fader_send_compensated_pre_fader_not(self):
        self.build([
            fx.track("GroupTrack", 1, "g", vol=0.5),
            fx.track("AudioTrack", 2, "x", group=1, sends=[0.4, 0.4], out="AudioOut/GroupTrack"),
        ], returns=2, sends_pre=[False, True])
        _, out = self.run_tool("--range", "none")
        amounts = [sp.send_amount(h) for h in sp.send_holders(self.track(out, "x"))]
        self.assertAlmostEqual(amounts[0], 0.8)
        self.assertAlmostEqual(amounts[1], 0.4)

    def test_group_off_mutes_children(self):
        self.build([
            fx.track("GroupTrack", 1, "g", on=False),
            fx.track("AudioTrack", 2, "x", group=1, out="AudioOut/GroupTrack"),
        ])
        _, out = self.run_tool("--range", "none")
        self.assertFalse(sp.is_on(self.track(out, "x")))


class FlattenEffects(Base):
    def make(self, child_vol=lin(-12)):
        g = fx.track("GroupTrack", 1, "drums", vol=lin(-6),
                     devices=[fx.device("Compressor2", 0, ("Threshold",)), fx.device("AutoFilter", 1)])
        k = fx.track("AudioTrack", 2, "kick", group=1, vol=child_vol, out="AudioOut/GroupTrack",
                     devices=[fx.device("Eq8", 0), fx.utility(1)])
        fx.automate(g, sp.target_id(g.find("DeviceChain/DeviceChain/Devices/AutoFilter/Gain")))
        return g, k

    def test_effects_copied_after_child_level_kept_in_utility(self):
        g, k = self.make()
        self.build([g, k])
        _, out = self.run_tool("--range", "none")
        kick = self.track(out, "kick")
        self.assertEqual(sp.device_tags(kick), ["Eq8", "StereoGain", "StereoGain", "Compressor2", "AutoFilter"])
        pre = list(sp.devices_el(kick))[2]
        self.assertEqual(pre.find("UserName").get("Value"), sp.PRE_GAIN_NAME)
        self.assertAlmostEqual(sp.db(float(pre.find("Gain/Manual").get("Value"))), -12, places=4)
        self.assertEqual(pre.find("StereoWidth/Manual").get("Value"), "1")  # template state reset
        self.assertDb(kick, -6)
        list_ids = [d.get("Id") for d in sp.devices_el(kick)]
        self.assertEqual(len(list_ids), len(set(list_ids)))
        self.assertEqual(sp.validate(out), [])

    def test_group_automation_follows_copied_device(self):
        g, k = self.make()
        self.build([g, k])
        _, out = self.run_tool("--range", "none")
        kick = self.track(out, "kick")
        af = sp.devices_el(kick).find("AutoFilter")
        self.assertIn(sp.target_id(af.find("Gain")), sp.envelope_pointees(kick))

    def test_child_volume_automation_moves_to_utility(self):
        g, k = self.make()
        fx.automate(k, sp.target_id(sp.mixer(k).find("Volume")))
        self.build([g, k])
        _, out = self.run_tool("--range", "none")
        kick = self.track(out, "kick")
        pre = list(sp.devices_el(kick))[2]
        pointees = sp.envelope_pointees(kick)
        self.assertIn(sp.target_id(pre.find("Gain")), pointees)
        self.assertNotIn(sp.target_id(sp.mixer(kick).find("Volume")), pointees)
        self.assertDb(kick, -6)

    def test_pre_fader_send_follows_pre_gain_utility(self):
        g = fx.track("GroupTrack", 1, "g", vol=lin(-6), devices=[fx.device("Saturator")])
        k = fx.track("AudioTrack", 2, "x", group=1, vol=0.5, sends=[0.2], out="AudioOut/GroupTrack",
                     devices=[fx.utility(0)])
        self.build([g, k], returns=1, sends_pre=[True])
        _, out = self.run_tool("--range", "none")
        self.assertAlmostEqual(sp.send_amount(sp.send_holders(self.track(out, "x"))[0]), 0.4)

    def test_post_fader_send_with_pre_gain_utility(self):
        # child fader -11.5 dB, group -6 dB with effects: send must stay at the same absolute level
        g = fx.track("GroupTrack", 1, "g", vol=0.5, devices=[fx.device("Saturator")])
        k = fx.track("AudioTrack", 2, "x", group=1, vol=lin(-11.5), sends=[0.4], out="AudioOut/GroupTrack",
                     devices=[fx.utility(0)])  # also provides the Utility template
        self.build([g, k], returns=1, sends_pre=[False])
        _, out = self.run_tool("--range", "none")
        t = self.track(out, "x")
        self.assertAlmostEqual(sp.volume(t), 0.5)  # Utility carries the child level
        # original tap = child level * 0.4; new tap = utility * group fader * new send
        new = lin(-11.5) * sp.volume(t) * sp.send_amount(sp.send_holders(t)[0])
        self.assertAlmostEqual(new, lin(-11.5) * 0.4, places=6)

    def test_modulation_effects_are_flagged(self):
        g = fx.track("GroupTrack", 1, "pads", devices=[fx.device("Chorus2")])
        k = fx.track("AudioTrack", 2, "pad", group=1, out="AudioOut/GroupTrack")
        self.build([g, k])
        report, _ = self.run_tool("--range", "none")
        self.assertTrue(any("modulation" in w for w in report["groups"][0]["warnings"]))

    def test_vocal_group_effects_not_copied(self):
        self.build([
            fx.track("GroupTrack", 1, "Vox Bus", vol=lin(-2), devices=[fx.device("Reverb")]),
            fx.track("AudioTrack", 2, "lead", group=1, vol=lin(-4), out="AudioOut/GroupTrack"),
        ])
        report, out = self.run_tool("--range", "none")
        lead = self.track(out, "lead")
        self.assertEqual(sp.device_tags(lead), [])
        self.assertDb(lead, -6)
        self.assertTrue(any("NOT copied" in w for w in report["groups"][0]["warnings"]))

    def test_strip_vocal_fx_removes_devices_and_their_automation(self):
        lead = fx.track("AudioTrack", 2, "vox lead", devices=[fx.device("Eq8")])
        fx.automate(lead, sp.target_id(lead.find("DeviceChain/DeviceChain/Devices/Eq8/Gain")))
        self.build([lead])
        _, out = self.run_tool("--range", "none", "--strip-vocal-fx")
        t = self.track(out, "vox lead")
        self.assertEqual(sp.device_tags(t), [])
        self.assertEqual(sp.envelope_pointees(t), [])


class Refusals(Base):
    def test_group_used_as_sidechain_source_is_kept(self):
        self.build([
            fx.track("GroupTrack", 1, "kick bus"),
            fx.track("AudioTrack", 2, "kick", group=1, out="AudioOut/GroupTrack"),
            fx.track("AudioTrack", 3, "bass", inputs=["AudioIn/Track.1/PostFxOut"]),
        ])
        report, out = self.run_tool("--range", "none")
        self.assertEqual(report["kept"][0]["group"], "kick bus")
        self.assertIn("audio source", report["kept"][0]["reason"])
        self.assertEqual(len(sp.all_tracks(out)), 3)

    def test_automated_or_panned_or_sending_group_is_kept(self):
        g1 = fx.track("GroupTrack", 1, "auto")
        fx.automate(g1, sp.target_id(sp.mixer(g1).find("Volume")))
        g2 = fx.track("GroupTrack", 2, "panned", pan=0.3)
        g3 = fx.track("GroupTrack", 3, "sending", sends=[0.5])
        self.build([g1, g2, g3], returns=1)
        report, _ = self.run_tool("--range", "none")
        self.assertEqual(sorted(k["group"] for k in report["kept"]), ["auto", "panned", "sending"])

    def test_gain_over_fader_maximum_is_refused_without_changes(self):
        self.build([
            fx.track("GroupTrack", 1, "hot", vol=lin(4)),
            fx.track("AudioTrack", 2, "a", group=1, vol=lin(1), out="AudioOut/GroupTrack"),
            fx.track("AudioTrack", 3, "b", group=1, vol=lin(4), out="AudioOut/GroupTrack"),
        ])
        report, out = self.run_tool("--range", "none")
        self.assertEqual(report["kept"][0]["group"], "hot")
        self.assertDb(self.track(out, "a"), 1)

    def test_never_overwrites_input_or_existing_output(self):
        self.build([fx.track("AudioTrack", 1, "a")])
        with self.assertRaises(sp.StemPrepError):
            self.run_tool("-o", self.src)
        self.run_tool()
        with self.assertRaises(sp.StemPrepError):
            self.run_tool()

    def test_dry_run_writes_nothing(self):
        self.build([fx.track("AudioTrack", 1, "a")])
        args = sp.build_parser().parse_args(["run", self.src, "--dry-run"])
        report = sp.run(args)
        self.assertFalse(os.path.exists(report["output"]))


    def test_old_live_versions_refused(self):
        root = fx.live_set([fx.track("AudioTrack", 1, "a")])
        root.set("Creator", "Ableton Live 10.1.30")
        fx.write(root, self.src)
        with self.assertRaises(sp.StemPrepError):
            self.run_tool()


class RangeAndNames(Base):
    def test_auto_range_pads_two_bars_before_four_after(self):
        self.build([
            fx.track("AudioTrack", 1, "a", clips=[(16, 40)]),
            fx.track("AudioTrack", 2, "b", clips=[(30, 78)]),
            fx.track("AudioTrack", 3, "muted ref", on=False, clips=[(0, 400)]),
        ])
        _, out = self.run_tool()
        ts = out.find("LiveSet/TimeSelection")
        self.assertEqual(float(ts.find("AnchorTime").get("Value")), 8)    # bar 5 - 2 bars
        self.assertEqual(float(ts.find("OtherTime").get("Value")), 96)    # 78 -> bar end 80, + 4 bars

    def test_range_clamped_at_song_start(self):
        self.build([fx.track("AudioTrack", 1, "a", clips=[(0, 16)])])
        report, out = self.run_tool()
        self.assertEqual(float(out.find("LiveSet/TimeSelection/AnchorTime").get("Value")), 0)
        self.assertIn("no room", report["range"])

    def test_rename_by_name_and_id(self):
        self.build([fx.track("AudioTrack", 1, "Audio 1"), fx.track("AudioTrack", 2, "Audio 2")])
        m = os.path.join(self.tmp.name, "map.json")
        with open(m, "w") as f:
            f.write('{"Audio 1": "kick1", "#2": "hh loop"}')
        _, out = self.run_tool("--range", "none", "--rename", m)
        self.assertEqual([sp.name(t) for t in sp.all_tracks(out)], ["kick1", "hh loop"])


class Validate(Base):
    def test_validate_catches_duplicate_list_ids(self):
        root = fx.live_set([fx.track("AudioTrack", 1, "a", devices=[fx.device("Eq8", 0), fx.device("Eq8", 0)])])
        self.assertTrue(any("list ids" in p for p in sp.validate(root)))


if __name__ == "__main__":
    unittest.main()
