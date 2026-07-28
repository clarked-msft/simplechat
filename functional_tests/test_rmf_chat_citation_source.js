#!/usr/bin/env node
/**
 * Functional test for RMF chat citation source-detail lookup.
 * Version: 0.250.075
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

const response = {
  content: "AC-2 is supported [1], while IA-5 is supported [2].",
  citations: [
    {
      id: "citation:1",
      index: 1,
      title: "Account Management",
      page: 12,
      source_id: "evidence:1"
    },
    {
      id: "citation:2",
      index: 2,
      label: "Authenticator Management",
      section: "3.4",
      metadata: {location: "Assessment workbook"},
      source_id: "evidence:2"
    }
  ]
};
const inlineReferences = [...response.content.matchAll(/\[(\d+)\]/g)]
  .map((match) => match[1]);
assert.deepEqual(inlineReferences, ["1", "2"]);

const sourceKey = citations.sourceKeyEntries(response.citations);
assert.equal(sourceKey.length, 2);
assert.deepEqual(
  sourceKey.map((entry) => entry.index),
  ["1", "2"]
);
assert.equal(sourceKey[0].label, "Account Management");
assert.equal(sourceKey[0].location, "Page 12");
assert.equal(sourceKey[1].label, "Authenticator Management");
assert.equal(sourceKey[1].location, "Section 3.4 · Assessment workbook");
console.log("RMF chat citation source lookup test passed.");
