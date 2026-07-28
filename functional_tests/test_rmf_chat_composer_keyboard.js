#!/usr/bin/env node
/**
 * Functional test for RMF chat composer keyboard behavior.
 * Version: 0.250.076
 * Implemented in: 0.250.076
 *
 * This test verifies plain Enter requests form submission while Shift+Enter and
 * IME composition preserve native textarea behavior.
 */

"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

const composer = require(path.resolve(
    __dirname,
    "../application/single_app/static/js/rmf/rmf-chat-composer.js"
));

function keyboardEvent(overrides = {}) {
    return {
        key: "Enter",
        shiftKey: false,
        isComposing: false,
        keyCode: 13,
        defaultPrevented: false,
        preventDefault() {
            this.defaultPrevented = true;
        },
        ...overrides
    };
}

let submitRequests = 0;
const form = {
    requestSubmit() {
        submitRequests += 1;
    }
};

const enter = keyboardEvent();
assert.equal(composer.handleKeydown(enter, form), true);
assert.equal(enter.defaultPrevented, true);
assert.equal(submitRequests, 1);

for (const ignoredEvent of [
    keyboardEvent({shiftKey: true}),
    keyboardEvent({isComposing: true}),
    keyboardEvent({keyCode: 229}),
    keyboardEvent({key: "Escape"})
]) {
    assert.equal(composer.handleKeydown(ignoredEvent, form), false);
    assert.equal(ignoredEvent.defaultPrevented, false);
}
assert.equal(submitRequests, 1);

console.log("RMF chat composer keyboard test passed.");
