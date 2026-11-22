// Minimal Streamlit custom-component bridge (same messages the official library sends).
export const embedded = window.parent !== window;

function send(type, data = {}) {
  window.parent.postMessage({ isStreamlitMessage: true, type, ...data }, "*");
}

// Python -> React: called every time Streamlit re-renders the component with new arguments
export function connect(onArgs) {
  const handler = (event) => {
    if (event.data && event.data.type === "streamlit:render") onArgs(event.data.args || {});
  };
  window.addEventListener("message", handler);
  send("streamlit:componentReady", { apiVersion: 1 });
  return () => window.removeEventListener("message", handler);
}

// React -> Python: the value Python receives from the component call
export const sendValue = (value) => send("streamlit:setComponentValue", { value, dataType: "json" });

export const setFrameHeight = (height) => send("streamlit:setFrameHeight", { height });
