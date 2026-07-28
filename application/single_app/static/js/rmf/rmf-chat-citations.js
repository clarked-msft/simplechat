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

  const citationApi = Object.freeze({sourceDetailsPath});
  globalScope.RmfChatCitations = citationApi;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = citationApi;
  }
})(typeof window === "undefined" ? globalThis : window);
