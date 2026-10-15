"""Exercise real display playback callbacks without audio hardware."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Browser playback checks require Node.js")
def test_receipts_require_actual_source_completion_and_ignore_cancelled_audio():
    display = Path(__file__).resolve().parents[1] / "cortex/avatar/display.html"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const code = html.slice(html.indexOf('    let audioCtx = null;'),
                        html.indexOf('    // When web-satellite overlay is active'));
const receipts = [], sources = [];
class Context {
  constructor() { this.sampleRate = 24000; this.currentTime = 0; }
  createBuffer(channels, size, rate) {
    return { duration: size / rate, getChannelData: () => new Float32Array(size) };
  }
  createBufferSource() {
    const source = { connect() {}, start() {}, stop() { this.onended?.(); } };
    sources.push(source);
    return source;
  }
}
const noop = () => {};
const context = {
  window: { AudioContext: Context, dispatchEvent: event => receipts.push(event.detail) },
  CustomEvent: class { constructor(name, options) { this.detail = options.detail; } },
  currentExpr: 'neutral', statusEl: {}, atob, console: { log: noop },
  performance, setTimeout: () => 1, clearTimeout: noop,
  _clearVisemeTimers: noop, scheduleSyncedVisemes: noop, doTalkPulse: noop,
};
vm.createContext(context);
vm.runInContext(code, context);
const tags = { playback_id: 'one', turn_id: 'turn', revision: 1 };
const chunk = { ...tags, audio: Buffer.alloc(8).toString('base64') };
context.handleTtsStart(tags);
context.handleTtsChunk(chunk);
context.handleTtsEnd(tags);
assert.equal(receipts.length, 0, 'TTS_END is not a playback receipt');
sources[0].onended();
assert.equal(receipts.length, 1);
sources[0].onended();
assert.equal(receipts.length, 1, 'receipts are idempotent');
context.handleTtsStart(tags);
context.handleTtsChunk(chunk);
vm.runInContext('_stopAllScheduledAudio()', context);
context.handleTtsEnd(tags);
assert.equal(receipts.length, 1, 'cancelled audio must never be acknowledged');
const newer = { ...tags, playback_id: 'two', revision: 2 };
context.handleTtsStart(newer);
context.handleTtsChunk(chunk);
assert.equal(sources.length, 2, 'stale chunks are ignored');
context.handleTtsChunk({ ...chunk, ...newer });
sources[2].onended();
assert.equal(receipts.length, 1, 'drain must also wait for TTS_END');
context.handleTtsEnd(newer);
assert.equal(receipts.length, 2);
"""
    result = subprocess.run(
        ["node", "-", str(display)], input=script, text=True,
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
