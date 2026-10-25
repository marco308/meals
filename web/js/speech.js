// Reading cook-mode steps aloud with the browser's own speech synthesis.
// Nothing leaves the device: the voices are the operating system's, and on a
// Mac or iPhone the downloadable Siri, Premium and Enhanced ones are far
// better than the default (the same lesson gymtrack's coach learned). So the
// best installed English voice is picked, and the choice is remembered on
// this browser only, because which voices are installed differs per device.
//
// What is said is the step's `spoken` text from the server ("200 grams",
// "half a teaspoon"), so every client says a recipe the same way.

const VOICE_KEY = "yamp.cook.voice";
const RATE_KEY = "yamp.cook.rate";

const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;

export const speechAvailable = () => Boolean(synth && window.SpeechSynthesisUtterance);

function quality(voice) {
  const name = `${voice.name} ${voice.voiceURI}`.toLowerCase();
  if (name.includes("premium")) return 3;
  if (name.includes("enhanced") || name.includes("siri")) return 2;
  // Novelty and legacy voices are listed beside the real ones and sound robotic.
  if (/eloquence|bells|bubbles|boing|jester|organ|trinoids|whisper|zarvox|bahh|albert|cellos|wobble/.test(name)) return -1;
  return voice.localService ? 1 : 0;
}

// English voices, best first: quality, then British, then the browser's language.
export function rankedVoices() {
  if (!speechAvailable()) return [];
  const own = (navigator.language || "en-GB").toLowerCase();
  return synth
    .getVoices()
    .filter((voice) => voice.lang.toLowerCase().startsWith("en") && quality(voice) >= 0)
    .sort((a, b) => {
      const byQuality = quality(b) - quality(a);
      if (byQuality) return byQuality;
      const gb = (v) => (v.lang.toLowerCase() === "en-gb" ? 1 : 0);
      if (gb(b) - gb(a)) return gb(b) - gb(a);
      const mine = (v) => (v.lang.toLowerCase() === own ? 1 : 0);
      if (mine(b) - mine(a)) return mine(b) - mine(a);
      return a.name.localeCompare(b.name);
    });
}

// Voices arrive asynchronously in Chrome; call back once they have.
export function onVoices(callback) {
  if (!speechAvailable()) return;
  if (synth.getVoices().length) callback();
  synth.addEventListener("voiceschanged", callback);
}

export function chosenVoice() {
  const voices = rankedVoices();
  let wanted = null;
  try {
    wanted = localStorage.getItem(VOICE_KEY);
  } catch {}
  return voices.find((voice) => voice.voiceURI === wanted) || voices[0] || null;
}

export function chooseVoice(uri) {
  try {
    if (uri) localStorage.setItem(VOICE_KEY, uri);
    else localStorage.removeItem(VOICE_KEY);
  } catch {}
}

export function rate() {
  try {
    const value = Number(localStorage.getItem(RATE_KEY));
    return value >= 0.6 && value <= 1.4 ? value : 1;
  } catch {
    return 1;
  }
}

export function setRate(value) {
  try {
    localStorage.setItem(RATE_KEY, String(value));
  } catch {}
}

// Only standard voices installed: worth saying how to get a better one.
export const onlyBasicVoices = () => !rankedVoices().some((voice) => quality(voice) >= 2);

export function stop() {
  if (speechAvailable()) synth.cancel();
}

export const speaking = () => speechAvailable() && (synth.speaking || synth.pending);

// One utterance per sentence: Chrome silently stops a long utterance about
// fifteen seconds in, and a step can easily run longer than that.
export function speak(text, { onEnd } = {}) {
  if (!speechAvailable() || !text) return;
  synth.cancel();
  const voice = chosenVoice();
  const sentences = text.match(/[^.!?;]+[.!?;]*\s*/g) || [text];
  sentences.forEach((sentence, index) => {
    const utterance = new SpeechSynthesisUtterance(sentence.trim());
    if (voice) {
      utterance.voice = voice;
      utterance.lang = voice.lang;
    } else {
      utterance.lang = "en-GB";
    }
    utterance.rate = rate();
    if (index === sentences.length - 1 && onEnd) utterance.onend = onEnd;
    synth.speak(utterance);
  });
}
