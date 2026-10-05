"""Write a small synthetic "Demo Song.als" to try the tool on: python3 examples/make_demo_set.py OUT_DIR"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
import fixtures as fx  # noqa: E402


def db(v):
    return 10 ** (v / 20.0)


def main(out_dir):
    drums = fx.track("GroupTrack", 1, "drums", vol=db(-6), devices=[fx.device("GlueCompressor"), fx.device("AutoFilter", 1)])
    kick = fx.track("AudioTrack", 2, "kick", group=1, vol=db(-10.9), out="AudioOut/GroupTrack",
                    devices=[fx.device("Eq8"), fx.utility(1)], clips=[(64, 704)])
    hats = fx.track("AudioTrack", 3, "hats", group=1, vol=db(-11.5), pan=0.2, out="AudioOut/GroupTrack", clips=[(128, 640)])
    kbus = fx.track("GroupTrack", 4, "kick bus", group=1, out="AudioOut/GroupTrack")
    sub = fx.track("AudioTrack", 5, "sub kick", group=4, vol=db(-8), out="AudioOut/GroupTrack", clips=[(64, 704)])
    bass = fx.track("MidiTrack", 6, "bass", vol=db(-13.4), inputs=["AudioIn/Track.4/PostFxOut"], clips=[(96, 700)])
    vox = fx.track("GroupTrack", 7, "vox", vol=db(-1), devices=[fx.device("Reverb")])
    lead = fx.track("AudioTrack", 8, "vox lead", group=7, vol=db(-19.7), out="AudioOut/GroupTrack", clips=[(160, 600)])
    dub = fx.track("AudioTrack", 9, "Audio 9", group=7, vol=db(-24), out="AudioOut/GroupTrack", clips=[(160, 600)])
    root = fx.live_set([drums, kick, hats, kbus, sub, bass, vox, lead, dub])
    path = os.path.join(out_dir, "Demo Song.als")
    fx.write(root, path)
    print(path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
