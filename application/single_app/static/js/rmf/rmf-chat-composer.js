// rmf-chat-composer.js
(function exposeRmfChatComposer(root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.RmfChatComposer = api;
    }
})(typeof window !== "undefined" ? window : globalThis, function createRmfChatComposer() {
    "use strict";

    function handleKeydown(event, form) {
        const isImeComposition = event.isComposing || event.keyCode === 229;
        if (event.key !== "Enter" || event.shiftKey || isImeComposition) {
            return false;
        }

        event.preventDefault();
        form.requestSubmit();
        return true;
    }

    return {handleKeydown};
});
