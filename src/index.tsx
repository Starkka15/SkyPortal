// SkyPortal quick access panel: portal status, figures on the portal, the figure library
// (~/Skylanders) and dumping figures from a real portal.
// License: GPL-3.0-or-later.

import { callable, definePlugin, toaster } from "@decky/api";
import {
  ButtonItem,
  Dropdown,
  Field,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { GiCrystalBall } from "react-icons/gi";

interface Loaded {
  slot: number;
  name: string;
  file: string;
  kind: string;
}

interface State {
  real_portal: boolean;
  real_error: string;
  real_slots: number[];
  real_toys: { slot: number; name: string }[];
  game_connected: boolean;
  active: boolean;
  dumping: boolean;
  loaded: Loaded[];
  events: string[];
  library_dir: string;
  dump_progress: string | null;
  dump_result: { ok: boolean; error?: string; figures?: { name: string; file: string }[] } | null;
}

interface Folder {
  folder: string;
  figures: string[];
  files: string[];
}

interface ElementGroup {
  group: string;
  figures: { name: string; file: string; folder: string; kind: string }[];
}

interface Result {
  ok: boolean;
  error?: string;
  slot?: number;
  name?: string;
}

const getState = callable<[], State>("get_state");
const getLibrary = callable<[], Folder[]>("get_library");
const getLibraryByElement = callable<[], ElementGroup[]>("get_library_by_element");

// Remembered between panel openings (per device; falls back to the default if storage fails).
function loadPref(key: string, fallback: string): string {
  try {
    return localStorage.getItem("skyportal." + key) ?? fallback;
  } catch {
    return fallback;
  }
}
function savePref(key: string, value: string) {
  try {
    localStorage.setItem("skyportal." + key, value);
  } catch {
    // ignore
  }
}
const place = callable<[file: string], Result>("place");
const lift = callable<[slot: number], Result>("lift");
const liftAll = callable<[], Result>("lift_all");
const dump = callable<[], Result>("dump");

function Content() {
  const [state, setState] = useState<State | null>(null);
  const [library, setLibrary] = useState<Folder[]>([]);
  const [byElement, setByElement] = useState<ElementGroup[]>([]);
  const [view, setView] = useState<string>(() => loadPref("view", "element"));
  const [folder, setFolder] = useState<string>(() => loadPref("folder", ""));
  const [group, setGroup] = useState<string>(() => loadPref("group", ""));
  const [lastDump, setLastDump] = useState<string>("");

  const refreshLibrary = async () => {
    const [lib, els] = await Promise.all([getLibrary(), getLibraryByElement()]);
    setLibrary(lib);
    setByElement(els);
    setFolder((f) => (lib.length && !lib.some((x) => x.folder === f) ? lib[0].folder : f));
    setGroup((g) => (els.length && !els.some((x) => x.group === g) ? els[0].group : g));
  };

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const st = await getState();
        if (alive) setState(st);
      } catch (e) {
        // backend restarting; try again next tick
      }
    };
    tick();
    refreshLibrary();
    const id = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Report a finished dump once, then show the new files in the library.
  useEffect(() => {
    const r = state?.dump_result;
    if (!r) return;
    const key = JSON.stringify(r);
    if (key === lastDump) return;
    setLastDump(key);
    if (r.ok) {
      const names = (r.figures || []).map((f) => f.name).join(", ");
      toaster.toast({ title: "SkyPortal", body: `Dumped ${names}` });
      refreshLibrary();
    } else {
      toaster.toast({ title: "SkyPortal dump failed", body: r.error || "unknown error" });
    }
  }, [state?.dump_result]);

  const onPlace = async (file: string) => {
    const r = await place(file);
    if (!r.ok) toaster.toast({ title: "SkyPortal", body: r.error || "could not place figure" });
    setState(await getState());
  };

  const onLift = async (slot: number) => {
    await lift(slot);
    setState(await getState());
  };

  const portalLine = !state
    ? "starting..."
    : state.real_portal
      ? `Real portal (${state.real_slots.length} toy${state.real_slots.length === 1 ? "" : "s"} on it)`
      : state.real_error
        ? `Emulated (real portal error: ${state.real_error})`
        : "Emulated (no real portal plugged in)";

  const onPortal = new Set((state?.loaded || []).map((l) => l.file));
  // One list for either view: {key, label, description, file}.
  const currentFolder = library.find((f) => f.folder === folder);
  const currentGroup = byElement.find((g) => g.group === group);
  const entries =
    view === "element"
      ? (currentGroup?.figures || []).map((f) => ({
          file: f.file,
          label: f.name,
          description: f.folder ? `${f.kind} - ${f.folder}` : f.kind,
        }))
      : (currentFolder?.files || []).map((file, i) => ({
          file: currentFolder!.folder ? `${currentFolder!.folder}/${file}` : file,
          label: currentFolder!.figures[i],
          description: "",
        }));

  return (
    <>
      <PanelSection title="Portal">
        <PanelSectionRow>
          <Field label="Portal" description={portalLine} />
        </PanelSectionRow>
        {state?.real_toys.map((t) => (
          <PanelSectionRow key={`real${t.slot}`}>
            <Field label={t.name || "Reading toy..."} description={`On the real portal, slot ${t.slot}`} />
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <Field
            label="Game"
            description={
              !state
                ? "-"
                : state.game_connected
                  ? state.active
                    ? "Connected, portal active"
                    : "Connected"
                  : "Not running"
            }
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="On the portal">
        {state && state.loaded.length === 0 && (
          <PanelSectionRow>
            <Field description="No figures from dumps placed." />
          </PanelSectionRow>
        )}
        {state?.loaded.map((l) => (
          <PanelSectionRow key={l.slot}>
            <ButtonItem
              layout="below"
              label={l.name}
              description={`${l.kind}, slot ${l.slot}`}
              onClick={() => onLift(l.slot)}
            >
              Lift off
            </ButtonItem>
          </PanelSectionRow>
        ))}
        {state && state.loaded.length > 1 && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { await liftAll(); setState(await getState()); }}>
              Lift all
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Figure library">
        {library.length === 0 && (
          <PanelSectionRow>
            <Field description={`No dumps yet. Put .sky/.bin/.dump files in ${state?.library_dir || "~/Skylanders"}.`} />
          </PanelSectionRow>
        )}
        {library.length > 0 && (
          <PanelSectionRow>
            <Dropdown
              rgOptions={[
                { data: "element", label: "By element" },
                { data: "folder", label: "By folder" },
              ]}
              selectedOption={view}
              onChange={(o) => {
                setView(o.data);
                savePref("view", o.data);
              }}
            />
          </PanelSectionRow>
        )}
        {view === "element" && byElement.length > 0 && (
          <PanelSectionRow>
            <Dropdown
              rgOptions={byElement.map((g) => ({ data: g.group, label: `${g.group} (${g.figures.length})` }))}
              selectedOption={group}
              onChange={(o) => {
                setGroup(o.data);
                savePref("group", o.data);
              }}
            />
          </PanelSectionRow>
        )}
        {view === "folder" && library.length > 1 && (
          <PanelSectionRow>
            <Dropdown
              rgOptions={library.map((f) => ({ data: f.folder, label: f.folder || "(top folder)" }))}
              selectedOption={folder}
              onChange={(o) => {
                setFolder(o.data);
                savePref("folder", o.data);
              }}
            />
          </PanelSectionRow>
        )}
        {entries.map((e) => {
          const placed = onPortal.has(e.file);
          return (
            <PanelSectionRow key={e.file}>
              <ButtonItem
                layout="below"
                disabled={placed}
                description={e.description || undefined}
                onClick={() => onPlace(e.file)}
              >
                {e.label}
                {placed ? " (on the portal)" : ""}
              </ButtonItem>
            </PanelSectionRow>
          );
        })}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={refreshLibrary}>
            Refresh library
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Dump figures">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!state?.real_portal || !!state?.dump_progress}
            description={
              state?.dump_progress
                ? `Reading: ${state.dump_progress}`
                : "Saves every toy on the real portal to Dumped/"
            }
            onClick={async () => {
              const r = await dump();
              if (!r.ok) toaster.toast({ title: "SkyPortal", body: r.error || "dump failed" });
            }}
          >
            Dump toys on the portal
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      {state && state.events.length > 0 && (
        <PanelSection title="Recent">
          {state.events.slice(-4).map((e, i) => (
            <PanelSectionRow key={i}>
              <Field description={e} />
            </PanelSectionRow>
          ))}
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => ({
  name: "SkyPortal",
  titleView: <div className={staticClasses.Title}>SkyPortal</div>,
  content: <Content />,
  icon: <GiCrystalBall />,
}));
