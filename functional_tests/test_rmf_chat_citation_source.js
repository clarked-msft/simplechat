#!/usr/bin/env node
/**
 * Functional test for RMF chat citation source-detail lookup.
 * Version: 0.250.073
 * Implemented in: 0.250.073
 *
 * This test ensures source-detail requests resolve the persisted ChatCitation.id
 * rather than the distinct underlying evidence/graph/assessment source_id.
 */

"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

const citations = require(path.resolve(
  __dirname,
  "../application/single_app/static/js/rmf/rmf-chat-citations.js"
));

const requestPath = citations.sourceDetailsPath(
  "conversation:7",
  {
    id: "citation:opaque-12",
    source_id: "evidence:underlying-99"
  }
);

assert.equal(
  requestPath,
  "/api/rmf/workspace/chat/sessions/conversation%3A7/sources/citation%3Aopaque-12"
);
assert.ok(!requestPath.includes("evidence%3Aunderlying-99"));
console.log("RMF chat citation source lookup test passed.");
