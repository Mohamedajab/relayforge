const button = document.querySelector("#run");
const status = document.querySelector("#status");
const timeline = document.querySelector("#timeline");
const failure = document.querySelector("#error");
const state = document.querySelector("#state");

function show(event) {
  const row = document.createElement("li");
  const clock = document.createElement("time");
  clock.textContent = typeof event.elapsed_ms === "number" ? (event.elapsed_ms / 1000).toFixed(2) + "s" : "—";
  const message = document.createElement("span");
  message.textContent = event.message;
  row.append(clock, message);
  timeline.append(row);
  if (event.attempt) document.querySelector("#attempts").textContent = event.attempt;
  if (event.stage === "attempt") state.textContent = event.status === 202 ? "Delivered" : "Retrying";
  if (event.stage === "error") throw new Error(event.message);
  if (event.stage === "verified") {
    row.classList.add("success");
    document.querySelector("#received").textContent = event.received;
    state.textContent = "Verified";
    status.textContent = "Finished. Three attempts, one receiver record.";
  }
}

button.addEventListener("click", async () => {
  button.disabled = true;
  failure.hidden = true;
  timeline.replaceChildren();
  document.querySelector("#attempts").textContent = "0";
  document.querySelector("#received").textContent = "0";
  state.textContent = "Running";
  status.textContent = "Running the Python recovery checks…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch("/api/run", { method: "POST", signal: controller.signal });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new Error(problem.error || "Demo unavailable. Try again shortly.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      const frames = pending.split("\n\n");
      pending = frames.pop();
      for (const frame of frames) {
        if (frame.startsWith("data: ")) show(JSON.parse(frame.slice(6)));
      }
      if (done) break;
    }
    if (state.textContent !== "Verified") throw new Error("The connection ended before verification.");
  } catch (error) {
    state.textContent = "Incomplete";
    failure.textContent = error.name === "AbortError" ? "The run timed out. Try again shortly." : error.message;
    failure.hidden = false;
    status.textContent = "The checks did not finish successfully.";
    controller.abort();
  } finally {
    clearTimeout(timer);
    button.disabled = false;
  }
});
