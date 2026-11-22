/**
 * Read an answer aloud with the browser's own speech synthesis: one voice, natural pacing, and
 * only the words a person would say. Markup, codes written for the eye and separators are
 * rewritten or dropped; nothing leaves the browser.
 */

/* Voices that sound natural, best first. Voices installed on the device come first: Chrome's
   online voices can fail and silently fall back to another voice. */
const PREFERRED = [
  /samantha/i,
  /microsoft (aria|jenny|guy).*natural/i,
  /microsoft (aria|jenny|zira|david)/i,
  /natural/i,
  /google us english/i,
  /google uk english female/i,
  /karen|daniel|moira|tessa|alex/i,
];

/** Bumped by every speak and stop, so a stop also cancels a reading still waiting for voices. */
let generation = 0;

/** The one voice for the whole session, chosen once the browser has listed its voices. */
let chosen: SpeechSynthesisVoice | null = null;

export function speechAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function pick(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis
    .getVoices()
    .filter((v) => v.lang.toLowerCase().startsWith("en"));
  if (voices.length === 0) return null; // not listed yet
  const ordered = [
    ...voices.filter((v) => v.localService),
    ...voices.filter((v) => !v.localService),
  ];
  for (const pattern of PREFERRED) {
    const found = ordered.find((v) => pattern.test(v.name));
    if (found) return found;
  }
  return ordered.find((v) => v.default) ?? ordered[0] ?? null;
}

/** The session's voice; waits (briefly) for the browser to list its voices the first time. */
function voice(): Promise<SpeechSynthesisVoice | null> {
  if (chosen) return Promise.resolve(chosen);
  chosen = pick();
  if (chosen) return Promise.resolve(chosen);
  return new Promise((resolve) => {
    const synth = window.speechSynthesis;
    const done = () => {
      synth.removeEventListener("voiceschanged", done);
      chosen = chosen ?? pick();
      resolve(chosen);
    };
    synth.addEventListener("voiceschanged", done);
    window.setTimeout(done, 1500);
  });
}

/** The text as a person would say it. */
export function spoken(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*|\*(.+?)\*/g, "$1$2") // markup
    .replace(/^\s*[-*•]\s+/gm, "") // list markers; each item is its own sentence
    .replace(/\n+/g, ". ")
    .replace(/\bSKU-(\w+)/g, "SKU $1")
    .replace(/\b(SO|PO)-(\d{8})-(\w+)/g, "$3")
    .replace(/\b([A-Z]\d+)-(\d{2})-(\d{2})\b/g, "$1, $2, $3") // bin codes, read in parts
    .replace(/\s*·\s*/g, ", ")
    .replace(/(\d),(\d{3})/g, "$1$2") // 12,685 reads as a number
    .replace(/\s*%/g, " percent")
    .replace(/\bLKR\b/g, "rupees")
    .replace(/\.\s*\./g, ".")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** Speaks `text`; stops anything already speaking. `onEnd` runs when it finishes or is stopped. */
export function speak(text: string, onEnd: () => void): void {
  if (!speechAvailable()) return;
  const synth = window.speechSynthesis;
  synth.cancel();
  const mine = ++generation;
  void voice().then((v) => {
    if (mine !== generation) return; // stopped or replaced while waiting
    const utterance = new SpeechSynthesisUtterance(spoken(text));
    say(utterance, v, onEnd);
  });
}

function say(
  utterance: SpeechSynthesisUtterance,
  v: SpeechSynthesisVoice | null,
  onEnd: () => void,
) {
  const synth = window.speechSynthesis;
  if (v) {
    utterance.voice = v;
    utterance.lang = v.lang;
  }
  utterance.rate = 1;
  utterance.pitch = 1;
  utterance.onend = onEnd;
  utterance.onerror = onEnd;
  synth.speak(utterance);
}

export function stopSpeaking(): void {
  generation += 1;
  if (speechAvailable()) window.speechSynthesis.cancel();
}
