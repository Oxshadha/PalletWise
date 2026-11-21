/**
 * Read an answer aloud with the browser's own speech synthesis: one voice, natural pacing, and
 * only the words a person would say. Markup, codes written for the eye and separators are
 * rewritten or dropped; nothing leaves the browser.
 */

/* Voices that sound natural, best first; the first one this browser has is used for every answer. */
const PREFERRED = [
  /natural/i,
  /google us english/i,
  /samantha/i,
  /google uk english female/i,
  /microsoft (aria|jenny|guy)/i,
  /karen|daniel|moira|tessa/i,
];

let chosen: SpeechSynthesisVoice | null = null;

export function speechAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function voice(): SpeechSynthesisVoice | null {
  if (chosen) return chosen;
  const voices = window.speechSynthesis
    .getVoices()
    .filter((v) => v.lang.toLowerCase().startsWith("en"));
  for (const pattern of PREFERRED) {
    const found = voices.find((v) => pattern.test(v.name));
    if (found) return (chosen = found);
  }
  return (chosen = voices.find((v) => v.default) ?? voices[0] ?? null);
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
  const utterance = new SpeechSynthesisUtterance(spoken(text));
  const v = voice();
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
  if (speechAvailable()) window.speechSynthesis.cancel();
}
