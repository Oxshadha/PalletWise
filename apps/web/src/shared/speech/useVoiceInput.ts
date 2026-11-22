import { useCallback, useEffect, useRef, useState } from "react";

/* The browser's speech recognition (Chrome, Edge, Safari). Typed here: not in every DOM library. */
interface RecognitionResult {
  readonly isFinal: boolean;
  readonly 0: { readonly transcript: string };
}
interface RecognitionEvent {
  readonly resultIndex: number;
  readonly results: { readonly length: number; readonly [i: number]: RecognitionResult };
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}
type RecognitionClass = new () => Recognition;

function recognitionClass(): RecognitionClass | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionClass;
    webkitSpeechRecognition?: RecognitionClass;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function voiceInputAvailable(): boolean {
  return typeof window !== "undefined" && recognitionClass() !== null;
}

export const BARS = 40;

/** idle; starting (asking for the microphone); listening; finishing (the last words arriving). */
export type VoicePhase = "idle" | "starting" | "listening" | "finishing";

/**
 * Listening: the words as they are recognised (final and in progress) and the microphone's level
 * for the last BARS frames. Audio goes only to the browser's own recogniser.
 */
export function useVoiceInput() {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [finalText, setFinalText] = useState("");
  const [interim, setInterim] = useState("");
  const [levels, setLevels] = useState<number[]>(() => Array<number>(BARS).fill(0));
  const [error, setError] = useState<string | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const audio = useRef<{ context: AudioContext; stream: MediaStream; frame: number } | null>(null);

  const stopAudio = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    cancelAnimationFrame(a.frame);
    a.stream.getTracks().forEach((t) => {
      t.stop();
    });
    void a.context.close();
    audio.current = null;
  }, []);

  const meter = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      const state = { context, stream, frame: 0 };
      audio.current = state;
      let last = 0;
      const tick = (now: number) => {
        state.frame = requestAnimationFrame(tick);
        if (now - last < 60) return;
        last = now;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const v of data) sum += ((v - 128) / 128) ** 2;
        const level = Math.min(1, Math.sqrt(sum / data.length) * 4);
        setLevels((prev) => [...prev.slice(1), level]);
      };
      state.frame = requestAnimationFrame(tick);
    } catch {
      /* no level meter; recognition reports its own errors */
    }
  }, []);

  const stop = useCallback(() => {
    setPhase((p) => (p === "idle" ? p : "finishing"));
    recognition.current?.stop();
    stopAudio();
  }, [stopAudio]);

  const cancel = useCallback(() => {
    recognition.current?.abort();
    stopAudio();
    setFinalText("");
    setInterim("");
  }, [stopAudio]);

  const start = useCallback(() => {
    const Klass = recognitionClass();
    if (!Klass) return;
    const r = new Klass();
    r.lang = navigator.language || "en-US";
    r.continuous = true;
    r.interimResults = true;
    let committed = "";
    r.onresult = (event) => {
      let pending = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result) continue;
        if (result.isFinal) committed += result[0].transcript;
        else pending += result[0].transcript;
      }
      setFinalText(committed.trim());
      setInterim(pending.trim());
    };
    r.onerror = (event) => {
      setError(
        event.error === "not-allowed" || event.error === "service-not-allowed"
          ? "Microphone access is blocked. Allow it in the browser to ask by voice."
          : event.error === "no-speech"
            ? null
            : "Voice input stopped. Try again or type the question.",
      );
    };
    r.onstart = () => {
      setPhase("listening");
    };
    r.onend = () => {
      setPhase("idle");
      setInterim("");
      stopAudio();
    };
    recognition.current = r;
    setFinalText("");
    setInterim("");
    setError(null);
    setLevels(Array<number>(BARS).fill(0));
    setPhase("starting");
    r.start();
    void meter();
  }, [meter, stopAudio]);

  useEffect(
    () => () => {
      recognition.current?.abort();
      stopAudio();
    },
    [stopAudio],
  );

  const transcript = [finalText, interim].filter(Boolean).join(" ");
  return { phase, transcript, interim, levels, error, start, stop, cancel };
}
