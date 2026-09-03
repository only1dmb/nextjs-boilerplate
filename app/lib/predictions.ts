export type PredictionCategory = "love" | "career" | "wealth" | "health" | "random";

export const CATEGORIES: { value: PredictionCategory; label: string; emoji: string }[] = [
  { value: "random", label: "Surprise me", emoji: "✨" },
  { value: "love", label: "Love", emoji: "💜" },
  { value: "career", label: "Career", emoji: "💼" },
  { value: "wealth", label: "Wealth", emoji: "💰" },
  { value: "health", label: "Health", emoji: "🌱" },
];

const TIMEFRAMES = [
  "before the next full moon",
  "within 3 days",
  "by the end of the month",
  "when you least expect it",
  "before this year is out",
  "on a Tuesday",
  "sooner than you think",
  "after a long wait",
];

const PLACES = [
  "a place you've never been",
  "somewhere oddly familiar",
  "your own backyard",
  "a crowded room",
  "a quiet corner of the internet",
  "a city that isn't home",
];

const THINGS = [
  "an old song",
  "a stranger's kindness",
  "a message you almost ignored",
  "a coin flip",
  "a coincidence too strange to ignore",
  "a door you thought was locked",
];

const TEMPLATES: Record<Exclude<PredictionCategory, "random">, string[]> = {
  love: [
    "{name}, someone is thinking about you right now, {timeframe}.",
    "{name}, an old flame resurfaces {timeframe} — you decide what happens next.",
    "{name}, your next meaningful connection begins with {thing}.",
    "{name}, the person you overlook today matters more {timeframe}.",
  ],
  career: [
    "{name}, an opportunity disguised as extra work arrives {timeframe}.",
    "{name}, a decision you're avoiding gets made for you {timeframe}.",
    "{name}, your next big break starts with {thing}, in {place}.",
    "{name}, someone finally notices the effort you put in, {timeframe}.",
  ],
  wealth: [
    "{name}, unexpected money finds you {timeframe} — don't spend it all at once.",
    "{name}, a small risk pays off bigger than expected, {timeframe}.",
    "{name}, {thing} leads to an opportunity worth more than it looks.",
    "{name}, your finances shift for the better {timeframe}.",
  ],
  health: [
    "{name}, your energy returns {timeframe} — rest until then.",
    "{name}, a habit you start today pays off {timeframe}.",
    "{name}, listen to what your body has been telling you about {thing}.",
    "{name}, a small change in routine changes more than expected, {timeframe}.",
  ],
};

function pick<T>(items: readonly T[]): T {
  return items[Math.floor(Math.random() * items.length)];
}

export function generatePrediction(name: string, category: PredictionCategory): string {
  const resolvedCategory =
    category === "random"
      ? pick(Object.keys(TEMPLATES) as Exclude<PredictionCategory, "random">[])
      : category;

  const template = pick(TEMPLATES[resolvedCategory]);
  const displayName = name.trim() || "Traveler";

  return template
    .replaceAll("{name}", displayName)
    .replaceAll("{timeframe}", pick(TIMEFRAMES))
    .replaceAll("{place}", pick(PLACES))
    .replaceAll("{thing}", pick(THINGS));
}
