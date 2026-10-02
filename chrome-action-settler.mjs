// Keep native Sky requests and URL policy unchanged. Only defer Chrome capture
// until a short post-action interval has elapsed. Never retry a native error.
import { setTimeout as sleep } from 'node:timers/promises';

const installed = new WeakSet();
const inputMethods = [
  'activate_window', 'click', 'press_key', 'type_text', 'scroll', 'drag',
  'set_value', 'perform_secondary_action',
];

function chromeWindowKey(input) {
  const window = input?.window;
  if (!window || !Number.isInteger(window.id) || window.id < 0 ||
      typeof window.app !== 'string' ||
      !/^(?:Chrome|Google Chrome)$|(?:^|[\\/])chrome\.exe$/i.test(window.app)) {
    return null;
  }
  return JSON.stringify([window.app.toLowerCase(), window.id]);
}

export function installChromeSettler(client, {
  settleMs = 2000,
  clock = { now: () => performance.now(), sleep },
} = {}) {
  if (installed.has(client)) return;
  if (!Number.isFinite(settleMs) || settleMs < 0 || settleMs > 5000) {
    throw new TypeError('settleMs must be between 0 and 5000');
  }
  if (typeof client.get_window_state !== 'function') {
    throw new TypeError('A native Sky Windows client is required');
  }
  const deadlines = new Map();
  for (const name of inputMethods) {
    const original = client[name];
    if (typeof original !== 'function') continue;
    client[name] = async function(input) {
      const result = await original.call(this, input);
      const key = chromeWindowKey(input);
      if (key !== null) {
        deadlines.set(key, clock.now() + settleMs);
        if (deadlines.size > 64) deadlines.delete(deadlines.keys().next().value);
      }
      return result;
    };
  }
  const capture = client.get_window_state;
  client.get_window_state = async function(input) {
    const key = chromeWindowKey(input);
    if (key !== null) {
      for (;;) {
        const remaining = (deadlines.get(key) ?? 0) - clock.now();
        if (remaining <= 0) break;
        await clock.sleep(remaining);
      }
      deadlines.delete(key);
    }
    return capture.call(this, input);
  };
  installed.add(client);
}
