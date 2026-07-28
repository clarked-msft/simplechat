(function initializeRmfChat() {
  "use strict";

  const root = document.querySelector("[data-rmf-chat-workspace]");
  if (!root) return;

  const elements = {
    layout: root.querySelector("[data-rmf-chat-layout]"),
    historyToggle: root.querySelector("[data-rmf-history-toggle]"),
    historyLoading: root.querySelector("[data-rmf-history-loading]"),
    activeConversations: root.querySelector("[data-rmf-active-conversations]"),
    archivedConversations: root.querySelector("[data-rmf-archived-conversations]"),
    newChatButtons: root.querySelectorAll("[data-rmf-new-chat], [data-rmf-stale-new-chat]"),
    archiveButton: root.querySelector("[data-rmf-archive-chat]"),
    title: root.querySelector("[data-rmf-chat-title]"),
    error: root.querySelector("[data-rmf-chat-error]"),
    staleBanner: root.querySelector("[data-rmf-stale-banner]"),
    intro: root.querySelector("[data-rmf-chat-intro]"),
    messages: root.querySelector("[data-rmf-chat-messages]"),
    retrieval: root.querySelector("[data-rmf-chat-retrieval]"),
    form: root.querySelector("[data-rmf-chat-form]"),
    question: root.querySelector("[data-rmf-chat-question]"),
    sendButton: root.querySelector("[data-rmf-chat-send]"),
    composerStatus: root.querySelector("[data-rmf-composer-status]"),
    characterCount: root.querySelector("[data-rmf-character-count]"),
    capabilityBadges: root.querySelector("[data-rmf-capability-badges]"),
    capabilityMessage: root.querySelector("[data-rmf-capability-message]"),
    sourceCounts: root.querySelector("[data-rmf-source-counts]"),
    starters: root.querySelector("[data-rmf-starters]"),
    starterPrompts: root.querySelector("[data-rmf-starter-prompts]"),
    contextSystem: root.querySelector("[data-rmf-context-system]"),
    contextImpact: root.querySelector("[data-rmf-context-impact]"),
    contextBaseline: root.querySelector("[data-rmf-context-baseline]"),
    sourceDrawer: document.querySelector("[data-rmf-source-drawer]"),
    sourceTitle: document.querySelector("[data-rmf-source-title]"),
    sourceContent: document.querySelector("[data-rmf-source-content]")
  };

  const state = {
    capabilities: null,
    sessions: [],
    conversation: null,
    busy: false,
    loadingConversation: false,
    conversationRequestId: 0,
    sourceRequestId: 0
  };

  class RmfChatApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "RmfChatApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  async function apiRequest(path, options) {
    const response = await fetch(path, options);
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      if (response.ok) return {};
    }
    if (!response.ok) {
      throw new RmfChatApiError(
        payload.error || payload.detail || "The RMF chat request failed.",
        response.status,
        payload
      );
    }
    return payload;
  }

  function messageForChatError(error) {
    if (error.status === 422) {
      return `RMF could not accept this question. ${error.message}`;
    }
    if (error.status === 429) {
      return `The RMF workspace is still busy after a safe retry. ${error.message}`;
    }
    if (error.status === 503) {
      return `The RMF model is temporarily unavailable after a safe retry. ${error.message}`;
    }
    if (!Number.isInteger(error.status)) {
      return "The RMF chat response could not be confirmed after a safe retry. Try again.";
    }
    return error.message;
  }

  function isStaleConflict(error) {
    if (error.status !== 409) return false;
    const detail = [
      error.payload?.code,
      error.payload?.reason,
      error.message
    ].filter(Boolean).join(" ").toLowerCase();
    return /\b(stale|revision|snapshot)\b/.test(detail)
      || /assessment\s+(has\s+)?changed/.test(detail)
      || /start\s+a\s+new\s+chat/.test(detail);
  }

  function setGlobalError(message) {
    elements.error.textContent = message || "";
    elements.error.classList.toggle("d-none", !message);
  }

  function setBusy(busy) {
    state.busy = busy;
    elements.retrieval.classList.toggle("d-none", !busy);
    elements.newChatButtons.forEach((button) => {
      button.disabled = busy || state.capabilities?.enabled !== true;
    });
    if (elements.archiveButton) {
      elements.archiveButton.disabled = busy || state.loadingConversation;
    }
    updateComposer();
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit"
    }).format(date);
  }

  function readableLabel(value) {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function sessionsFromPayload(payload) {
    if (Array.isArray(payload)) return payload;
    return Array.isArray(payload?.sessions) ? payload.sessions : [];
  }

  function sortedSessions(sessions) {
    return [...sessions].sort((left, right) => {
      return new Date(right.updated_at || 0).getTime()
        - new Date(left.updated_at || 0).getTime();
    });
  }

  function updateSession(conversation) {
    const index = state.sessions.findIndex((item) => item.id === conversation.id);
    if (index === -1) {
      state.sessions.push(conversation);
    } else {
      state.sessions[index] = conversation;
    }
  }

  function historyButton(conversation) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list-group-item list-group-item-action rmf-history-item";
    button.classList.toggle("active", state.conversation?.id === conversation.id);
    button.setAttribute(
      "aria-current",
      state.conversation?.id === conversation.id ? "true" : "false"
    );

    const title = document.createElement("span");
    title.className = "rmf-history-item-title";
    title.textContent = conversation.title || "Untitled investigation";

    const meta = document.createElement("span");
    meta.className = "rmf-history-item-meta";
    const updated = document.createElement("span");
    updated.textContent = formatDate(conversation.updated_at);
    const stateLabel = document.createElement("span");
    stateLabel.textContent = conversation.is_stale
      ? "Stale"
      : conversation.status === "archived"
        ? "Archived"
        : `${conversation.messages?.length || 0} messages`;
    meta.append(updated, stateLabel);
    button.append(title, meta);
    button.addEventListener("click", () => loadConversation(conversation.id));
    return button;
  }

  function renderHistoryGroup(container, conversations, emptyMessage) {
    container.replaceChildren();
    if (!conversations.length) {
      const empty = document.createElement("div");
      empty.className = "px-3 py-2 small text-muted";
      empty.textContent = emptyMessage;
      container.appendChild(empty);
      return;
    }
    conversations.forEach((conversation) => {
      container.appendChild(historyButton(conversation));
    });
  }

  function renderHistory() {
    const sessions = sortedSessions(state.sessions);
    renderHistoryGroup(
      elements.activeConversations,
      sessions.filter((item) => item.status !== "archived"),
      "No active investigations."
    );
    renderHistoryGroup(
      elements.archivedConversations,
      sessions.filter((item) => item.status === "archived"),
      "No archived investigations."
    );
    elements.historyLoading.classList.add("d-none");
  }

  function citationButton(citation, conversationId) {
    const index = String(citation.index ?? "?");
    const sourceLabel = citation.title || citation.label || "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rmf-citation-chip";
    button.textContent = `[${index}]`;
    button.title = citation.title || citation.label || `Open source ${index}`;
    button.setAttribute(
      "aria-label",
      `Citation ${index}${sourceLabel ? `: ${sourceLabel}` : ""}. Open source details.`
    );
    button.addEventListener("click", () => {
      loadSourceDetails(conversationId, citation);
    });
    return button;
  }

  function renderSourcesKey(citations, conversationId) {
    const section = document.createElement("section");
    section.className = "rmf-sources-key";
    section.setAttribute("aria-label", "Sources for this response");

    const heading = document.createElement("h3");
    heading.className = "rmf-sources-key-heading";
    heading.textContent = "Sources";
    const list = document.createElement("ol");
    list.className = "rmf-sources-key-list";

    window.RmfChatCitations.sourceKeyEntries(citations).forEach((entry) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "rmf-source-key-button";
      const number = document.createElement("span");
      number.className = "rmf-source-key-number";
      number.textContent = `[${entry.index}]`;
      const description = document.createElement("span");
      description.className = "rmf-source-key-description";
      if (entry.label) {
        const sourceLabel = document.createElement("span");
        sourceLabel.className = "rmf-source-key-label";
        sourceLabel.textContent = entry.label;
        description.appendChild(sourceLabel);
      }
      if (entry.location) {
        const location = document.createElement("span");
        location.className = "rmf-source-key-location";
        location.textContent = entry.location;
        description.appendChild(location);
      }
      if (!entry.label && !entry.location) {
        description.textContent = "Open source details";
      }
      const accessibleDescription = [entry.label, entry.location]
        .filter(Boolean)
        .join(", ");
      button.setAttribute(
        "aria-label",
        `Open source details for citation ${entry.index}`
        + (accessibleDescription ? `: ${accessibleDescription}` : "")
      );
      button.append(number, description);
      button.addEventListener("click", () => {
        loadSourceDetails(conversationId, entry.citation);
      });
      item.appendChild(button);
      list.appendChild(item);
    });
    section.append(heading, list);
    return section;
  }

  function decorateInlineCitations(container, citations, conversationId) {
    const citationMap = new Map(
      citations.map((citation) => [String(citation.index), citation])
    );
    const referenced = new Set();
    const nodes = [];
    const walker = document.createTreeWalker(
      container,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode(node) {
          if (!node.nodeValue || !/\[\d+\]/.test(node.nodeValue)) {
            return NodeFilter.FILTER_REJECT;
          }
          if (node.parentElement?.closest("code, pre, a, button")) {
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        }
      }
    );
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach((node) => {
      const fragment = document.createDocumentFragment();
      const expression = /\[(\d+)\]/g;
      let cursor = 0;
      let match = expression.exec(node.nodeValue);
      while (match) {
        const citation = citationMap.get(match[1]);
        if (citation) {
          fragment.appendChild(
            document.createTextNode(node.nodeValue.slice(cursor, match.index))
          );
          fragment.appendChild(citationButton(citation, conversationId));
          referenced.add(match[1]);
          cursor = match.index + match[0].length;
        }
        match = expression.exec(node.nodeValue);
      }
      if (cursor === 0) return;
      fragment.appendChild(document.createTextNode(node.nodeValue.slice(cursor)));
      node.replaceWith(fragment);
    });
    return referenced;
  }

  function renderAssistantContent(container, content) {
    if (!window.marked || !window.DOMPurify) {
      container.textContent = content;
      return;
    }
    const sanitized = window.DOMPurify.sanitize(
      window.marked.parse(String(content || "")),
      {
        FORBID_TAGS: ["form", "iframe", "object", "embed"],
        FORBID_ATTR: ["style"]
      }
    );
    const template = document.createElement("template");
    template.innerHTML = sanitized;
    container.replaceChildren(template.content.cloneNode(true));
    container.querySelectorAll("a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
  }

  function renderMessage(message, conversationId) {
    const role = message.role === "assistant" ? "assistant" : "user";
    const article = document.createElement("article");
    article.className = `rmf-chat-message ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "rmf-chat-message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    const icon = document.createElement("i");
    icon.className = role === "assistant" ? "bi bi-shield-check" : "bi bi-person";
    avatar.appendChild(icon);

    const body = document.createElement("div");
    body.className = "min-width-0";
    const label = document.createElement("div");
    label.className = "rmf-chat-message-label";
    label.textContent = role === "assistant" ? "RMF Investigator" : "You";
    const content = document.createElement("div");
    content.className = "rmf-chat-message-content";
    if (role === "assistant") {
      renderAssistantContent(content, message.content || "");
    } else {
      content.textContent = message.content || "";
    }
    body.append(label, content);

    if (role === "assistant") {
      const citations = Array.isArray(message.citations) ? message.citations : [];
      decorateInlineCitations(content, citations, conversationId);
      if (citations.length) {
        body.appendChild(renderSourcesKey(citations, conversationId));
      }
      const insufficient = citations.length === 0
        || /insufficient evidence|not enough evidence|unable to determine/i.test(
          String(message.content || "")
        );
      if (insufficient) {
        const warning = document.createElement("div");
        warning.className = "rmf-evidence-warning";
        warning.textContent = citations.length === 0
          ? "No supporting evidence was returned. Treat this response as insufficiently grounded."
          : "The current RMF snapshot does not contain enough evidence for a complete answer.";
        body.appendChild(warning);
      }
    }

    article.append(avatar, body);
    return article;
  }

  function renderMessages() {
    const messages = Array.isArray(state.conversation?.messages)
      ? state.conversation.messages
      : [];
    const hasMessages = messages.length > 0;
    elements.intro.classList.toggle("d-none", hasMessages);
    elements.messages.classList.toggle("d-none", !hasMessages);
    elements.messages.replaceChildren();
    if (!hasMessages) return;
    messages.forEach((message) => {
      elements.messages.appendChild(renderMessage(message, state.conversation.id));
    });
    window.requestAnimationFrame(() => {
      elements.messages.scrollTop = elements.messages.scrollHeight;
    });
  }

  function updateHeader() {
    const conversation = state.conversation;
    elements.title.textContent = conversation?.title || "New investigation";
    const stale = conversation?.is_stale === true;
    elements.staleBanner.classList.toggle("d-none", !stale);
    elements.archiveButton.classList.toggle(
      "d-none",
      !conversation || conversation.status === "archived"
    );
    elements.archiveButton.disabled = state.busy || state.loadingConversation;
  }

  function updateComposer() {
    const conversation = state.conversation;
    const enabled = state.capabilities?.enabled === true;
    const active = conversation?.status === "active";
    const stale = conversation?.is_stale === true;
    const canSend = enabled
      && active
      && !stale
      && !state.busy
      && !state.loadingConversation;
    elements.question.disabled = !canSend;
    elements.sendButton.disabled = !canSend || !elements.question.value.trim();

    let status = "Select or start an active investigation to ask a question.";
    if (state.capabilities && !enabled) {
      status = state.capabilities.reason || "Investigative chat is not available.";
    } else if (state.loadingConversation) {
      status = "Loading the selected investigation...";
    } else if (stale) {
      status = "This snapshot is stale. Start a new chat to continue.";
    } else if (conversation?.status === "archived") {
      status = "This investigation is archived and remains read-only.";
    } else if (active) {
      status = "Grounded retrieval is read-only and uses the current RMF assessment snapshot.";
    }
    elements.composerStatus.textContent = status;
    elements.composerStatus.className = `alert py-2 px-3 mb-2 small ${
      stale ? "alert-warning" : active && enabled ? "alert-info" : "alert-secondary"
    }`;
  }

  function renderConversation(conversation) {
    state.conversation = conversation;
    updateSession(conversation);
    updateHeader();
    renderHistory();
    renderMessages();
    updateComposer();
  }

  function starterPromptText(prompt) {
    if (typeof prompt === "string") return prompt.trim();
    if (!prompt || typeof prompt !== "object") return "";
    return String(prompt.question || prompt.prompt || prompt.label || "").trim();
  }

  function renderCapabilities() {
    const capabilities = state.capabilities || {};
    elements.capabilityBadges.replaceChildren();
    [
      ["Read-only", "text-bg-primary"],
      ["Current snapshot", "text-bg-light border"],
      ["No live cloud tools", "text-bg-light border"]
    ].forEach(([label, className]) => {
      const badge = document.createElement("span");
      badge.className = `badge ${className}`;
      badge.textContent = label;
      elements.capabilityBadges.appendChild(badge);
    });

    const enabled = capabilities.enabled === true;
    elements.newChatButtons.forEach((button) => {
      button.disabled = !enabled || state.busy;
    });
    elements.capabilityMessage.classList.toggle("d-none", enabled);
    elements.capabilityMessage.textContent = enabled
      ? ""
      : capabilities.reason || "Investigative chat is not available for this workspace.";

    const counts = capabilities.source_counts;
    elements.sourceCounts.replaceChildren();
    if (counts && typeof counts === "object" && !Array.isArray(counts)) {
      Object.entries(counts).forEach(([key, value]) => {
        const badge = document.createElement("span");
        badge.className = "badge rounded-pill text-bg-light border";
        badge.textContent = `${readableLabel(key)}: ${Number(value) || 0}`;
        elements.sourceCounts.appendChild(badge);
      });
    }
    elements.sourceCounts.classList.toggle(
      "d-none",
      elements.sourceCounts.childElementCount === 0
    );

    const prompts = Array.isArray(capabilities.starter_prompts)
      ? capabilities.starter_prompts.map(starterPromptText).filter(Boolean)
      : [];
    elements.starterPrompts.replaceChildren();
    prompts.forEach((prompt) => {
      const column = document.createElement("div");
      column.className = "col-md-6";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "rmf-starter-prompt";
      button.textContent = prompt;
      button.disabled = !enabled;
      button.addEventListener("click", async () => {
        if (!state.conversation || state.conversation.status !== "active") {
          await createConversation();
        }
        if (!state.conversation || state.conversation.is_stale) return;
        elements.question.value = prompt;
        updateCharacterCount();
        updateComposer();
        elements.question.focus();
      });
      column.appendChild(button);
      elements.starterPrompts.appendChild(column);
    });
    elements.starters.classList.toggle("d-none", prompts.length === 0);
    updateComposer();
  }

  function renderContext(workspace, overview) {
    const system = overview?.system || workspace?.service?.system;
    elements.contextSystem.textContent = system?.name || root.dataset.groupName;
    if (system?.impact_level != null) {
      elements.contextImpact.textContent = `Impact ${system.impact_level}`;
      elements.contextImpact.classList.remove("d-none");
    }
    const baseline = overview?.baseline;
    if (baseline?.total_controls != null) {
      elements.contextBaseline.textContent =
        `${baseline.analyzed_controls || 0}/${baseline.total_controls} baseline controls`;
      elements.contextBaseline.classList.remove("d-none");
    }
  }

  async function loadContext() {
    const [workspaceResult, overviewResult] = await Promise.allSettled([
      apiRequest("/api/rmf/workspace"),
      apiRequest("/api/rmf/workspace/overview")
    ]);
    renderContext(
      workspaceResult.status === "fulfilled" ? workspaceResult.value : null,
      overviewResult.status === "fulfilled" ? overviewResult.value : null
    );
  }

  async function loadConversation(conversationId) {
    if (!conversationId || state.busy) return;
    const requestId = ++state.conversationRequestId;
    state.loadingConversation = true;
    elements.archiveButton.disabled = true;
    updateComposer();
    setGlobalError("");
    try {
      const conversation = await apiRequest(
        `/api/rmf/workspace/chat/sessions/${encodeURIComponent(conversationId)}`
      );
      if (requestId !== state.conversationRequestId) return;
      renderConversation(conversation);
      if (window.innerWidth < 992) setHistoryCollapsed(true);
    } catch (error) {
      if (requestId !== state.conversationRequestId) return;
      setGlobalError(error.message);
    } finally {
      if (requestId === state.conversationRequestId) {
        state.loadingConversation = false;
        elements.archiveButton.disabled = state.busy;
        updateComposer();
      }
    }
  }

  async function createConversation() {
    if (state.busy || state.capabilities?.enabled !== true) return null;
    state.conversationRequestId += 1;
    state.loadingConversation = false;
    setGlobalError("");
    setBusy(true);
    let created = null;
    try {
      const conversation = await apiRequest(
        "/api/rmf/workspace/chat/sessions",
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({})
        }
      );
      created = conversation;
      renderConversation(conversation);
      return conversation;
    } catch (error) {
      setGlobalError(
        error.status === 429 ? messageForChatError(error) : error.message
      );
      return null;
    } finally {
      setBusy(false);
      if (created) {
        window.requestAnimationFrame(() => elements.question.focus());
      }
    }
  }

  async function archiveConversation() {
    const conversation = state.conversation;
    if (
      !conversation
      || conversation.status !== "active"
      || state.busy
      || state.loadingConversation
    ) {
      return;
    }
    if (!window.confirm("Archive this RMF investigation?")) return;
    setGlobalError("");
    setBusy(true);
    try {
      const archived = await apiRequest(
        `/api/rmf/workspace/chat/sessions/${encodeURIComponent(conversation.id)}/archive`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({expected_revision: conversation.revision})
        }
      );
      renderConversation(archived);
    } catch (error) {
      if (error.status === 409) {
        renderConversation({...conversation, is_stale: true});
      }
      setGlobalError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function sendQuestion(event) {
    event.preventDefault();
    const conversation = state.conversation;
    const question = elements.question.value.trim();
    if (
      !conversation
      || conversation.status !== "active"
      || conversation.is_stale
      || !question
      || state.busy
    ) {
      return;
    }

    const previous = conversation;
    const optimistic = {
      ...conversation,
      messages: [
        ...(Array.isArray(conversation.messages) ? conversation.messages : []),
        {
          id: `pending-${Date.now()}`,
          role: "user",
          content: question,
          created_at: new Date().toISOString(),
          citations: []
        }
      ]
    };
    elements.question.value = "";
    updateCharacterCount();
    renderConversation(optimistic);
    setGlobalError("");
    setBusy(true);
    try {
      const updated = await window.RmfChatRetry.sendWithStableIdempotency(
        apiRequest,
        `/api/rmf/workspace/chat/sessions/${encodeURIComponent(conversation.id)}/messages`,
        {
          expected_revision: conversation.revision,
          question
        }
      );
      renderConversation(updated);
    } catch (error) {
      const staleConflict = isStaleConflict(error);
      const restored = staleConflict
        ? {...previous, is_stale: true}
        : previous;
      renderConversation(restored);
      if (!staleConflict) {
        elements.question.value = question;
        updateCharacterCount();
        window.requestAnimationFrame(() => elements.question.focus());
      }
      setGlobalError(messageForChatError(error));
    } finally {
      setBusy(false);
    }
  }

  function appendDefinition(list, label, value) {
    if (value === undefined || value === null || value === "") return;
    const term = document.createElement("dt");
    term.className = "col-sm-4";
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.className = "col-sm-8";
    detail.textContent = String(value);
    list.append(term, detail);
  }

  function renderSourceDetails(source) {
    elements.sourceTitle.textContent = source.title || source.label || "Source details";
    elements.sourceContent.replaceChildren();

    const summary = document.createElement("dl");
    summary.className = "row small";
    appendDefinition(summary, "Reference", source.index != null ? `[${source.index}]` : "");
    appendDefinition(summary, "Type", readableLabel(source.kind));
    appendDefinition(summary, "Page", source.page);
    appendDefinition(summary, "Section", source.section);
    elements.sourceContent.appendChild(summary);

    const heading = document.createElement("h3");
    heading.className = "h6 mt-3";
    heading.textContent = "Excerpt";
    const excerpt = document.createElement("div");
    excerpt.className = "rmf-source-excerpt border rounded p-3 bg-body-tertiary";
    excerpt.textContent = source.excerpt || "No excerpt is available.";
    elements.sourceContent.append(heading, excerpt);

    if (source.metadata && typeof source.metadata === "object") {
      const metadataHeading = document.createElement("h3");
      metadataHeading.className = "h6 mt-4";
      metadataHeading.textContent = "Metadata";
      const metadata = document.createElement("dl");
      metadata.className = "row small";
      Object.entries(source.metadata).forEach(([key, value]) => {
        appendDefinition(
          metadata,
          readableLabel(key),
          typeof value === "object" ? JSON.stringify(value) : value
        );
      });
      elements.sourceContent.append(metadataHeading, metadata);
    }
  }

  async function loadSourceDetails(conversationId, citation) {
    if (!citation.id) {
      setGlobalError("This citation does not include a citation identifier.");
      return;
    }
    const requestId = ++state.sourceRequestId;
    elements.sourceTitle.textContent = citation.title || `Source [${citation.index}]`;
    const loading = document.createElement("div");
    loading.className = "d-flex align-items-center gap-2 text-muted";
    const spinner = document.createElement("span");
    spinner.className = "spinner-border spinner-border-sm";
    spinner.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = "Loading source details...";
    loading.append(spinner, text);
    elements.sourceContent.replaceChildren(loading);

    if (window.bootstrap?.Offcanvas) {
      window.bootstrap.Offcanvas.getOrCreateInstance(elements.sourceDrawer).show();
    }
    try {
      const source = await apiRequest(
        window.RmfChatCitations.sourceDetailsPath(conversationId, citation)
      );
      if (requestId !== state.sourceRequestId) return;
      renderSourceDetails(source);
    } catch (error) {
      if (requestId !== state.sourceRequestId) return;
      const alert = document.createElement("div");
      alert.className = "alert alert-danger";
      alert.textContent = error.message;
      elements.sourceContent.replaceChildren(alert);
    }
  }

  function updateCharacterCount() {
    elements.characterCount.textContent = `${elements.question.value.length} / 10000`;
  }

  function setHistoryCollapsed(collapsed) {
    elements.layout.classList.toggle("history-collapsed", collapsed);
    elements.historyToggle.setAttribute("aria-expanded", String(!collapsed));
  }

  function configureHistoryControls() {
    elements.historyToggle.addEventListener("click", () => {
      setHistoryCollapsed(!elements.layout.classList.contains("history-collapsed"));
    });
    root.querySelectorAll("[data-rmf-history-section-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = document.getElementById(button.getAttribute("aria-controls"));
        const expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        target.classList.toggle("d-none", expanded);
        const icon = button.querySelector("i");
        icon.className = expanded ? "bi bi-chevron-right" : "bi bi-chevron-down";
      });
    });
    if (window.innerWidth < 992) setHistoryCollapsed(true);
  }

  async function initialize() {
    configureHistoryControls();
    elements.newChatButtons.forEach((button) => {
      button.addEventListener("click", createConversation);
    });
    elements.archiveButton.addEventListener("click", archiveConversation);
    elements.form.addEventListener("submit", sendQuestion);
    elements.question.addEventListener("input", () => {
      updateCharacterCount();
      updateComposer();
    });
    elements.question.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        elements.form.requestSubmit();
      }
    });

    loadContext();
    const [capabilitiesResult, sessionsResult] = await Promise.allSettled([
      apiRequest("/api/rmf/workspace/chat/capabilities"),
      apiRequest("/api/rmf/workspace/chat/sessions")
    ]);

    if (capabilitiesResult.status === "fulfilled") {
      state.capabilities = capabilitiesResult.value;
      renderCapabilities();
    } else {
      state.capabilities = {
        enabled: false,
        reason: capabilitiesResult.reason.message
      };
      renderCapabilities();
      setGlobalError(capabilitiesResult.reason.message);
    }

    if (sessionsResult.status === "fulfilled") {
      state.sessions = sessionsFromPayload(sessionsResult.value);
      renderHistory();
      const firstActive = sortedSessions(state.sessions).find(
        (conversation) => conversation.status !== "archived"
      );
      if (firstActive && state.capabilities.enabled) {
        await loadConversation(firstActive.id);
      }
    } else {
      elements.historyLoading.classList.add("d-none");
      setGlobalError(sessionsResult.reason.message);
    }
    updateComposer();
  }

  initialize();
})();
