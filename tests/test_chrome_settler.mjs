import assert from 'node:assert/strict';
import test from 'node:test';
import { installChromeSettler } from '../chrome-action-settler.mjs';

function fixture() {
  let now = 0;
  const calls = [];
  const result = { window: { app: 'Chrome', id: 1 }, screenshots: [] };
  const client = {
    target: 'windows',
    async press_key(input) { calls.push({ method: 'press_key', input, now }); return 'input-returned'; },
    async get_window_state(input) { calls.push({ method: 'capture', input, now }); return result; },
  };
  const clock = { now: () => now, async sleep(ms) { calls.push({ method: 'wait', ms }); now += ms; } };
  return { client, clock, calls, result, advance: ms => { now += ms; } };
}

test('Chrome capture waits after successful input and preserves native arguments and result', async () => {
  const f = fixture();
  installChromeSettler(f.client, { clock: f.clock });
  const input = { window: { app: 'Chrome', id: 1 }, key: 'Control_L+t' };
  const capture = { window: input.window, include_text: true, include_screenshot: true };
  assert.equal(await f.client.press_key(input), 'input-returned');
  assert.equal(await f.client.get_window_state(capture), f.result);
  assert.deepEqual(f.calls.map(c => c.method), ['press_key', 'wait', 'capture']);
  assert.equal(f.calls[1].ms, 2000);
  assert.equal(f.calls[2].now, 2000);
  assert.equal(f.calls[0].input, input);
  assert.equal(f.calls[2].input, capture);
  await f.client.get_window_state(capture);
  assert.equal(f.calls.filter(c => c.method === 'wait').length, 1);
});

test('other apps and other Chrome windows do not inherit a wait', async () => {
  const f = fixture();
  installChromeSettler(f.client, { clock: f.clock });
  await f.client.press_key({ window: { app: 'Brave', id: 1 }, key: 'Control_L+t' });
  await f.client.get_window_state({ window: { app: 'Brave', id: 1 } });
  await f.client.press_key({ window: { app: 'Chrome', id: 1 }, key: 'Control_L+t' });
  await f.client.get_window_state({ window: { app: 'Chrome', id: 2 } });
  assert.equal(f.calls.filter(c => c.method === 'wait').length, 0);
});

test('process-backed Chrome identifiers receive the same settling interval', async () => {
  const f = fixture();
  installChromeSettler(f.client, { clock: f.clock });
  const window = { app: 'process:C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', id: 7 };
  await f.client.press_key({ window, key: 'Control_L+t' });
  await f.client.get_window_state({ window });
  assert.equal(f.calls.find(c => c.method === 'wait').ms, 2000);
});

test('time already elapsed is not added again', async () => {
  const f = fixture();
  installChromeSettler(f.client, { clock: f.clock });
  const window = { app: 'Chrome', id: 1 };
  await f.client.press_key({ window, key: 'Control_L+t' });
  f.advance(2500);
  await f.client.get_window_state({ window });
  assert.equal(f.calls.filter(c => c.method === 'wait').length, 0);
});

test('native capture stop is propagated unchanged and is never retried', async () => {
  const f = fixture();
  const stop = new Error('Computer Use has been stopped for this turn');
  let captures = 0;
  f.client.get_window_state = async () => { captures += 1; throw stop; };
  installChromeSettler(f.client, { clock: f.clock });
  const window = { app: 'Chrome', id: 1 };
  await f.client.press_key({ window, key: 'Control_L+t' });
  await assert.rejects(f.client.get_window_state({ window }), error => error === stop);
  assert.equal(captures, 1);
});

test('physical Escape or native input failure is propagated with no recovery calls', async () => {
  const f = fixture();
  const stop = new Error('Computer Use was stopped by the user with the physical Escape key.');
  let attempts = 0;
  f.client.press_key = async () => { attempts += 1; throw stop; };
  installChromeSettler(f.client, { clock: f.clock });
  await assert.rejects(f.client.press_key({ window: { app: 'Chrome', id: 1 }, key: 'Control_L+t' }), error => error === stop);
  assert.equal(attempts, 1);
  assert.equal(f.calls.length, 0);
});

test('installation is idempotent and bounds its configurable interval', async () => {
  const f = fixture();
  installChromeSettler(f.client, { clock: f.clock });
  const once = f.client.get_window_state;
  installChromeSettler(f.client, { clock: f.clock });
  assert.equal(f.client.get_window_state, once);
  assert.throws(() => installChromeSettler(fixture().client, { settleMs: 6000 }), /settleMs/);
});
