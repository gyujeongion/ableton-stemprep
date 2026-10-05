---
name: abletonstem
description: Use when the user asks to prepare an Ableton Live set for stem export - flatten groups while keeping group gain and effects, keep vocals raw, name tracks for stem files, or set the export range. First choice edits the saved .als with ableton-stemprep; fallback drives the open set through Producer Pal.
---

# abletonstem: prepare a Live set for stem export

Goal: a set where *Export Audio/Video > All Individual Tracks* gives correct stems.

- every instrument group flattened, its gain and effects carried onto its tracks,
- vocals raw (no group effects copied onto them),
- track names that work as stem file names,
- time selection = song start - 2 bars to song end + 4 bars.

Never export or render, never save over the user's original set, never delete audio files.

## 1. First choice: edit the .als file (ableton-stemprep)

Install: `pip install git+https://github.com/gyujeongion/ableton-stemprep` (or run `ableton_stemprep.py` with python3).

1. **Make sure the file is current.** The .als on disk is the last saved state. If the user has unsaved changes in Live, ask them to save first.
2. `ableton-stemprep scan "<set>.als"` - read the tree: groups, gains, effect counts, vocal flags, frozen tracks, and the reasons a group cannot be flattened.
3. Decide what needs the user:
   - Vocal detection is a name regex. Check it: a "vocal" sample inside an instrument group, or a vocal track named "Audio 12", is misclassified. Correct with `--vocal NAME` / `--not-vocal NAME`. `--strip-vocal-fx` removes **all** devices from vocal audio tracks, so confirm the list first.
   - Groups with bus compression, limiting, saturation or sidechain ducking: copying them per track does not sound like the summed bus. Tell the user; do not call it "fully preserved".
   - Track names: build a `--rename map.json` only for names you can interpret from the clip/sample names (`vox lead`, `vox dub`, `kick1`, `hh loop`, `perc loop`). Leave unclear ones and list them.
   - Reference or guide tracks: pass `--range-exclude NAME` so they do not stretch the export range.
4. `ableton-stemprep run "<set>.als" --dry-run [options]` - show the user the plan in a few lines; ask only about the open decisions.
5. `ableton-stemprep run "<set>.als" [options]` - writes `<set> STEM.als` next to the original. Run `scan` on the output to confirm: no groups left except the reported ones, vocal tracks as intended, names.
6. The user opens the STEM set in Live, selects all tracks (selection is not stored in the file), listens against the original, and exports.

Groups the tool leaves untouched (automated group mixer, panned group, active group sends, group used as a sidechain source, gain over +6 dB) are reported with the reason. Either accept them as a stem bus, or use section 2 for them.

## 2. Fallback: drive the open set with Producer Pal

Use when the user wants to watch it happen in Live, or for groups the tool refused. Say why you are falling back.

Setup (once): Live 12.3+ with Max for Live, Node.js 20+, `Producer_Pal.amxd` from https://github.com/adamjmurray/producer-pal/releases in *User Library > Presets > MIDI Effects > Max MIDI Effect*, and the MCP server registered (`claude mcp add producer-pal npx producer-pal@latest`).

Per set:
- Work on a duplicate the user saved as `<set> STEM.als`. Confirm the open set's name before changing anything.
- Producer Pal is a MIDI effect: load it on an empty MIDI track named `NO EXPORT - Producer Pal`, muted, monitoring off, not armed. It will not load on audio tracks or the main track. Remove this track at the end.
- The device version inside the set and the `npx` server version can differ. Read back after every write; if a parameter is ignored, check which argument names this version accepts.
- Reading a whole large set can time out. Read track by track.
- An API selection is not proof of UI focus. Re-read the selected track id before keyboard shortcuts such as ungroup.

For each instrument group, innermost first:
1. Read the group's devices, mixer, automation, sends, output, and who takes audio from it.
2. No effects: add the group's gain to each child in dB (linear values multiply; never apply either to Live's normalized fader value), set the group to 0 dB, ungroup.
3. With effects: the original order is child devices > child fader > group devices > group fader. Put a Utility at the end of each child's chain with the child's old fader gain, copy the group devices after it (Live's copy/paste on the device title bar is more reliable than API device duplication in some versions), set the child fader to the group's gain, then ungroup.
4. Verify each child after the change: volume, pan, mute, routing, sends, device order, automation. A deactivated group must leave its children muted.

Vocal groups: ungroup with gain only, do not copy their effects. Remove inserts on vocal tracks only when the user asked for raw vocals; turning a device off is not enough if its on/off is automated. Effects printed into the audio (bounced or frozen) cannot be undone; report such tracks as processed sources.

Range: find the real first and last audible arrangement clip, excluding reference and trigger tracks, snap to bars, pad -2 / +4 bars, set the arrangement time selection (not the loop). If the song starts at bar 1, start at bar 1 and report it; do not shift the timeline.

## Report

```text
Set: <name>          Route: file edit / Producer Pal (reason)
Output: <absolute path to "<name> STEM.als">
Flattened: <groups with old gain -> applied gain, effects copied>
Left as is: <group: reason>
Vocals raw: <tracks>   Vocals still processed: <tracks + why>
Range: bar X to bar Y
Renamed: <old -> new>   Needs a name: <tracks>
Not done: export, render, saving over the original
```
