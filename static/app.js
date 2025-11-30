// connect websocket for live logs
(function(){
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/logs";
  let sock;
  try {
    sock = new WebSocket(url);
  } catch(e){
    return;
  }
  const box = document.getElementById("logsbox");
  function appendLine(s){
    if(!box) return;
    const el = document.createElement("div");
    el.textContent = typeof s === "string" ? s : JSON.stringify(s);
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  }
  sock.onopen = ()=> appendLine("[ws] connected");
  sock.onclose = ()=> appendLine("[ws] disconnected");
  sock.onerror = (e)=> appendLine("[ws] error");
  sock.onmessage = (evt)=>{
    try {
      const d = JSON.parse(evt.data);
      if(d.type === "action"){
        appendLine(`[ACTION] ${d.payload.ts_iso} ${d.payload.actor} ${d.payload.action} ${d.payload.details}`);
      } else if(d.type === "visit"){
        appendLine(`[VISIT] ${d.payload.ts_iso} ${d.payload.ip} ${d.payload.user} -> ${d.payload.path}`);
      } else if(d.type === "info"){
        appendLine(`[INFO] ${d.payload}`);
      } else {
        appendLine(evt.data);
      }
    } catch(e){
      appendLine(evt.data);
    }
  };

  const clearBtn = document.getElementById("clearLocal");
  if(clearBtn){
    clearBtn.addEventListener("click", ()=> {
      const box = document.getElementById("logsbox");
      if(box) box.innerHTML = "";
    });
  }
})();
