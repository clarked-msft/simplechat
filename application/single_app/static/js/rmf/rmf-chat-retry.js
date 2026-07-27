(function initializeRmfChatRetry(globalScope) {
  "use strict";

  function createIdempotencyKey(cryptoObject = globalScope.crypto) {
    if (typeof cryptoObject?.randomUUID === "function") {
      return cryptoObject.randomUUID();
    }
    if (typeof cryptoObject?.getRandomValues !== "function") {
      throw new Error("Secure UUID generation is unavailable.");
    }

    const bytes = cryptoObject.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return [
      hex.slice(0, 4).join(""),
      hex.slice(4, 6).join(""),
      hex.slice(6, 8).join(""),
      hex.slice(8, 10).join(""),
      hex.slice(10).join("")
    ].join("-");
  }

  async function sendWithStableIdempotency(
    apiRequest,
    path,
    message,
    cryptoObject = globalScope.crypto
  ) {
    const payload = {
      ...message,
      idempotency_key: createIdempotencyKey(cryptoObject)
    };
    const options = {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    };

    try {
      return await apiRequest(path, options);
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      return apiRequest(path, options);
    }
  }

  const retryApi = Object.freeze({
    createIdempotencyKey,
    sendWithStableIdempotency
  });
  globalScope.RmfChatRetry = retryApi;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = retryApi;
  }
})(typeof window === "undefined" ? globalThis : window);
