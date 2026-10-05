# ableton-stemprep

Prepare Ableton Live sets for stem export: flatten groups without losing their gain or effects, keep vocals raw, and set the export range. It writes a **new** `.als`; your set is never touched.

```text
$ ableton-stemprep run "Demo Song.als" --rename names.json
Flattened groups: 2
  drums (-6.0 dB) + effects copied: GlueCompressor, AutoFilter
    - kick: -10.9 dB -> -6.0 dB, pre-gain Utility -10.9 dB
    - hats: -11.5 dB -> -6.0 dB, pre-gain Utility -11.5 dB
    - kick bus: +0.0 dB -> -6.0 dB
    ! hats: its pan now comes after the copied group effects
    ! kick bus feeds bass (PostFxOut); that signal now includes the group effects
  vox (-1.0 dB) [vocal]
    - vox lead: -19.7 dB -> -20.7 dB
    - Audio 9: -24.0 dB -> -25.0 dB
    ! group effects NOT copied (vocal group, kept raw): Reverb

Groups left as they are: 1
  kick bus: used as an audio source by: bass

Renamed:
  Audio 9 -> vox dub

Export range: bar 15 to bar 181 in 4/4
```

(`python3 examples/make_demo_set.py` writes this demo set, so you can try the tool without touching a real project.)

Exporting stems with *All Individual Tracks* gives you one file per track, but a track inside a group is exported **before** the group's fader and effects. Bounce the groups instead and your mix engineer loses the separate parts. The usual fix is to ungroup by hand: move each group's gain onto its tracks, copy the group's effect chain onto every child, and leave the vocals dry. On a big session that is slow and easy to get wrong.

`ableton-stemprep` does it directly in the `.als` file:

- **Group gain** is folded into each child (dB added, nested groups counted once each).
- **Group effects** are copied onto every child, with their automation. The child's own fader level moves into a Utility (`STEM pre-gain`) placed in front of the copied effects, so a compressor or saturator gets the same input level as before. If the child's fader is automated, that automation moves onto the Utility too.
- **Vocal groups** (`vox`, `vocal`, `acapella`, ... or `--vocal NAME`) are flattened without copying their effects. Inserts on the vocal tracks themselves stay (and are listed) unless you add `--strip-vocal-fx`, which removes them for truly raw vocals.
- **Sends, mute, track delay**: send levels are corrected for the new fader value (post-fader) or the pre-gain Utility (pre-fader), a deactivated group mutes its children, a soloed group solos its children, group track delay is added to the children. Any active solo in the set is reported, because it silences everything else in a bounce. Tracks inside a group that are routed somewhere else are only moved out, not changed.
- **Export range**: the arrangement time selection is set to the song plus 2 bars before and 4 bars after, so *Export Audio/Video* is ready to go.
- **Renaming** from a JSON map, so the stem files come out named `kick1`, `hh loop`, `vox lead`, ...
- **Refuses instead of guessing.** A group is left untouched (and reported) when it cannot be flattened exactly: its mixer is automated, it is panned, it has active sends, another track takes audio from it (sidechain), or a child would need more than +6 dB.

Standard library only, Python 3.8+, one file.

> **Status: beta.** Tested on Live 12 sets (saved by 12.1, opened in 12.4) with groups, nested groups, plugins, racks and automation, plus a unit test suite. It does not touch your original, and a result that fails its structural checks is never written, but structural checks are not an audio comparison: listen to the stems (or null them against a bounce of the original, below) before you send them. Live 11 sets are untested, Live 10 and older are refused.

## Install

```bash
pip install git+https://github.com/gyujeongion/ableton-stemprep
# or download ableton_stemprep.py and run it with python3
```

## Usage

```bash
# 1. look at the track tree and what would block flattening
ableton-stemprep scan "My Song.als"

# 2. see the plan, write nothing
ableton-stemprep run "My Song.als" --dry-run

# 3. write "My Song STEM.als" next to the original
ableton-stemprep run "My Song.als"
```

Then open `My Song STEM.als` in Live, check it, select all tracks and use *File > Export Audio/Video > All Individual Tracks*. The time selection is already set.

**Check the result with a null test**: export the main output of the original set over the same range, import it and all stems into a new set, flip the phase of the original (Utility > Phase), and play. What you hear is the difference. Expect silence or near-silence where only gain and static effects were involved, and audible residue on groups with bus dynamics or modulation, which the report already named.

### Options

| Option | Meaning |
|---|---|
| `-o FILE` | output path (default `<name> STEM.als` next to the input) |
| `--keep NAME` / `--only NAME` | leave a group alone / flatten only these groups (repeatable, name or `#id`) |
| `--vocal NAME` / `--not-vocal NAME` | correct the vocal detection |
| `--vocal-pattern REGEX` | change the vocal name pattern |
| `--copy-vocal-fx` | copy vocal group effects too |
| `--strip-vocal-fx` | remove every device from vocal audio tracks |
| `--no-fx` | fold gain only, never copy effects |
| `--rename MAP.json` | `{"Audio 3": "kick1", "#42": "hh loop"}` |
| `--range auto\|none\|FROM:TO` | export range; `FROM:TO` in bars, e.g. `9:121` |
| `--range-exclude NAME` | ignore a reference or guide track when finding the song range |
| `--json` | machine-readable report |
| `--force` | replace an existing output file |

Exit code `0` on success, `2` on errors (nothing is written then).

## What is exact and what is not

| Situation | Result |
|---|---|
| Group with no effects | exact: gain folded into faders, sends compensated |
| Group with effects | each child goes through its own copy of the chain. Static EQ and filters match closely; **anything that reacts to the summed signal (bus compressor, limiter, saturation, sidechain ducking) behaves differently** on single tracks, and free-running modulation (chorus, phaser, flanger, auto pan) is flagged because separate copies may not stay in phase. The report names these devices per group (Compressor, Glue Compressor, Limiter, Saturator, Roar, ... and unknown plugins). In a render test, a group with only a static EQ and gain nulled to the 24-bit noise floor, while an EQ plus compressor group left an audible difference. Copying is a choice: `--keep-nonlinear` leaves such groups as one stem bus, `--keep NAME` does it for a named group, `--no-fx` gives dry stems. |
| Child panned, or with sends, inside a group with effects | pan and sends now come after the copied effects; reported per track |
| Group fader/pan/sends automated, group panned, group used as a sidechain source | group left as it is, reason reported |
| Frozen children of a group with effects | group left as it is (Live does not add effects to frozen tracks) |
| Tracks with the same name | reported, because the stem files would be ambiguous |

Nothing is exported or rendered, and the original set is never saved over.

## Second way: do it in Live with an AI agent (Producer Pal)

The file route above is the reliable one. If you would rather let an AI agent work on the set that is open in Live (to see each step, or for sets the tool refuses), the [`claude-skill/SKILL.md`](claude-skill/SKILL.md) procedure drives Live through **[Producer Pal](https://producer-pal.org)**, an open-source MCP server for Ableton Live by Adam Murray (GPL-3.0, not part of this project).

One-time setup:

1. **Requirements**: Live 12.3+ with Max for Live (12.4+ recommended), Node.js 20+, and an MCP client such as Claude Code or Claude Desktop.
2. **Install the device**: download [`Producer_Pal.amxd`](https://github.com/adamjmurray/producer-pal/releases/latest/download/Producer_Pal.amxd). In Live's browser open *Places > User Library > Presets > MIDI Effects > Max MIDI Effect* and drag the file into that folder.
3. **Register the MCP server** (Claude Code):
   ```bash
   claude mcp add producer-pal npx producer-pal@latest
   ```
   For other clients see [producer-pal.org/installation](https://producer-pal.org/installation).
4. **Per set**: duplicate the set first (*File > Save Live Set As...* `My Song STEM.als`). Create an empty MIDI track, name it `NO EXPORT - Producer Pal`, mute it, and drop `Producer_Pal.amxd` on it. It is a MIDI effect, so it will not load on an audio track or the main track. The device should show *Producer Pal Running*.
5. **Install the skill** (Claude Code): copy `claude-skill/` to `~/.claude/skills/abletonstem/` and ask *"prepare this set for stems"*.

Things that came up in practice: the device version and the `npx` server version can differ, so check every write by reading it back; reading a whole 100-track set can time out, read track by track; moving devices between tracks through the API was unreliable in one version, so the skill copies devices with Live's own copy/paste. Delete the `NO EXPORT` track before exporting.

## How it works

A `.als` file is gzip-compressed XML. Every track has a `TrackGroupId`, an output routing, a mixer and a device chain; every automatable parameter has a set-wide id that automation envelopes point to. The tool

1. builds the group tree and decides once which tracks are vocal,
2. flattens groups innermost first: re-parents the children, points `AudioOut/GroupTrack` outputs to the group's own output, folds the gain, deep-copies the group's devices with fresh parameter ids and duplicates their automation,
3. removes the group, sets the time selection and names,
4. checks the result (group references, routing, unique ids at every level, automation targets, `NextPointeeId`), writes it to a temporary file and renames it into place, then reads it back and checks again.

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT. Ableton and Ableton Live are trademarks of Ableton AG; this project is not affiliated with Ableton. Producer Pal is a separate project by Adam Murray.
