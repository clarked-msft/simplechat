// rmf-chat-citations.js
(function initializeRmfChatCitations(globalScope) {
  "use strict";

  function sourceDetailsPath(conversationId, citation) {
    if (!conversationId || !citation?.id) {
      throw new Error("Conversation and citation identifiers are required.");
    }
    return `/api/rmf/workspace/chat/sessions/${encodeURIComponent(conversationId)}/sources/`
      + encodeURIComponent(citation.id);
  }

  function sourceKeyEntries(citations) {
    if (!Array.isArray(citations)) return [];
    return citations.map((citation) => {
      const locations = [];
      if (citation.page !== undefined && citation.page !== null && citation.page !== "") {
        locations.push(`Page ${citation.page}`);
      }
      if (citation.section) locations.push(`Section ${citation.section}`);
      const location = citation.location || citation.metadata?.location;
      if (location) locations.push(String(location));
      return {
        citation,
        index: String(citation.index ?? "?"),
        label: citation.title || citation.label || "",
        location: locations.join(" · ")
      };
    });
  }

  const citationApi = Object.freeze({sourceDetailsPath, sourceKeyEntries});
  globalScope.RmfChatCitations = citationApi;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = citationApi;
  }
})(typeof window === "undefined" ? globalThis : window);
