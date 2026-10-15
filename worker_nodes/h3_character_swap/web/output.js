import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";

app.registerExtension({
  name: "h3.characterSwap.output",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "H3CharacterSwapAssemble") return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);
      const video = document.createElement("video");
      video.controls = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.style.cssText = "width:100%;height:100%;object-fit:contain;background:#111;border-radius:6px";
      this.h3OutputVideo = video;
      this.addDOMWidget("h3_output", "video", video, {
        serialize: false,
        getMinHeight: () => 200,
        getHeight: () => 240,
      });
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      executed?.apply(this, arguments);
      const file = message.h3_character_swap?.[0]?.output;
      if (file && this.h3OutputVideo) {
        this.h3OutputVideo.src = api.apiURL("/view?" + new URLSearchParams(file));
      }
    };
    const removed = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this.h3OutputVideo?.pause();
      this.h3OutputVideo?.removeAttribute("src");
      removed?.apply(this, arguments);
    };
  },
});
