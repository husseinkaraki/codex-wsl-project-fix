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

function mayNavigate(name, input) {
  if (name === 'click' || name === 'perform_secondary_action') return true;
  if (name !== 'press_key' || typeof input?.key !== 'string') return false;
  const keys = input.key.toLowerCase().split('+').map(key => key.trim());
  return keys.some(key => ['return', 'enter', 'kp_enter', 'numpad_enter', 'f5'].includes(key)) ||
    (keys.includes('r') && keys.some(key => /^(?:control(?:_[lr])?|ctrl)$/.test(key))) ||
    (keys.some(key => /^alt(?:_[lr])?$/.test(key)) &&
      keys.some(key => key === 'left' || key === 'right'));
}

export function installChromeSettler(client, {
  settleMs = 2000,
  navigationSettleMs = 5000,
  clock = { now: () => performance.now(), sleep },
} = {}) {
  if (installed.has(client)) return;
  if (!Number.isFinite(settleMs) || settleMs < 0 || settleMs > 5000) {
    throw new TypeError('settleMs must be between 0 and 5000');
  }
  if (!Number.isFinite(navigationSettleMs) || navigationSettleMs < 0 || navigationSettleMs > 5000) {
    throw new TypeError('navigationSettleMs must be between 0 and 5000');
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
        // A deployed page can redirect while Chrome rebuilds its accessible
        // document. This is a bounded pre-capture delay, not a readiness claim.
        const delay = mayNavigate(name, input) ? Math.max(settleMs, navigationSettleMs) : settleMs;
        deadlines.set(key, clock.now() + delay);
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
