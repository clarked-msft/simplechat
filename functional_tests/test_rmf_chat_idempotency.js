#!/usr/bin/env node
/**
 * Functional test for RMF chat idempotent transport retries.
 * Version: 0.250.072
 * Implemented in: 0.250.071
 *
 * This test verifies that one send reuses its UUID after a retryable transport
 * or HTTP failure, while a later send receives a different UUID.
 */

"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

const retry = require(path.resolve(
  __dirname,
  "../application/single_app/static/js/rmf/rmf-chat-retry.js"
));

async function testStableRetryKey() {
  let uuidSequence = 1;
  const cryptoObject = {
    randomUUID() {
      const suffix = String(uuidSequence++).padStart(12, "0");
      return `00000000-0000-4000-8000-${suffix}`;
    }
  };
  const requestBodies = [];
  let loseFirstResponse = true;
  async function apiRequest(_requestPath, options) {
    requestBodies.push(JSON.parse(options.body));
    if (loseFirstResponse) {
      loseFirstResponse = false;
      throw new TypeError("Failed to fetch");
    }
    return {id: "chat-1"};
  }

  await retry.sendWithStableIdempotency(
    apiRequest,
    "/messages",
    {expected_revision: 1, question: "What changed?"},
    cryptoObject,
    async () => {}
  );
  await retry.sendWithStableIdempotency(
    apiRequest,
    "/messages",
    {expected_revision: 2, question: "What remains?"},
    cryptoObject,
    async () => {}
  );

  assert.equal(requestBodies.length, 3);
  assert.equal(requestBodies[0].idempotency_key, requestBodies[1].idempotency_key);
  assert.notEqual(requestBodies[1].idempotency_key, requestBodies[2].idempotency_key);
  assert.equal(requestBodies[0].question, "What changed?");

  for (const status of [429, 503]) {
    const retryBodies = [];
    let retryCalls = 0;
    const retryableError = Object.assign(new Error("Temporary failure"), {status});
    await retry.sendWithStableIdempotency(
      async (_requestPath, options) => {
        retryCalls += 1;
        retryBodies.push(JSON.parse(options.body));
        if (retryCalls === 1) throw retryableError;
        return {id: "chat-1"};
      },
      "/messages",
      {expected_revision: 3, question: "Try this safely"},
      cryptoObject,
      async () => {}
    );
    assert.equal(retryCalls, 2);
    assert.equal(
      retryBodies[0].idempotency_key,
      retryBodies[1].idempotency_key
    );
  }

  let conflictCalls = 0;
  const conflict = Object.assign(new Error("Permanent limit reached"), {
    status: 409
  });
  await assert.rejects(
    retry.sendWithStableIdempotency(
      async () => {
        conflictCalls += 1;
        throw conflict;
      },
      "/messages",
      {expected_revision: 3, question: "One more?"},
      cryptoObject,
      async () => {}
    ),
    (error) => error === conflict
  );
  assert.equal(conflictCalls, 1);
}

testStableRetryKey()
  .then(() => {
    console.log("RMF chat idempotency retry test passed.");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
