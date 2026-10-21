const appWindow = window.parent;
const appDocument = appWindow.document;
/* The visible controls use native DOM; hidden Streamlit buttons own navigation. */
appWindow.__rhInitToolsCommandBar = function (config) {
  const rootId = config.rootId || "rh-tools-commandbar";
  const bridgePrefix = config.bridgePrefix || "tools-select-";
  const controllerSlot = config.controllerSlot || "__rhToolsCommandBar";
  const queryParam = config.queryParam === undefined ? "rh_tool" : config.queryParam;
  const toolbarLabel = config.toolbarLabel || "Research tools";
  const itemNoun = config.itemNoun || "tools";
  const roots = appDocument.querySelectorAll("#" + rootId);
  const root = roots[roots.length - 1];
  if (!root) {
    appWindow.setTimeout(() => appWindow.__rhInitToolsCommandBar(config), 30);
    return;
  }
  if (appWindow[controllerSlot]) appWindow[controllerSlot].destroy();
  const controller = new AbortController();
  const signal = controller.signal;
  const tools = config.groups.flatMap(group =>
    group.tools.map(tool => Object.assign({ group: group.label }, tool)));
  const byName = new Map(tools.map(tool => [tool.name, tool]));
  const current = byName.get(config.selected) || tools[0];
  let menuAnchor = null;
  let menuItems = [];
  let resultItems = [];
  let activeResult = 0;
  let returnFocus = null;

  function icon(name) {
    const span = appDocument.createElement("span");
    span.className = "material-symbols-rounded rh-tools-icon";
    span.textContent = name;
    span.setAttribute("aria-hidden", "true");
    return span;
  }
  function button(label, className) {
    const node = appDocument.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    return node;
  }
  function updateUrl(name, replace) {
    if (!queryParam) return;
    const url = new URL(appWindow.location.href);
    if (url.searchParams.get(queryParam) === name) return;
    url.searchParams.set(queryParam, name);
    appWindow.history[replace ? "replaceState" : "pushState"](
      Object.assign({}, appWindow.history.state, { rhCommandSelection: name }), "", url);
  }
  function navigate(name, push) {
    if (!byName.has(name)) return;
    closeMenu(false);
    closeSearch(false);
    if (push) updateUrl(name, false);
    const slug = name.toLowerCase().replace(/ /g, "-");
    const bridge = appDocument.querySelector(".st-key-" + bridgePrefix + slug + " button");
    if (bridge) bridge.click();
  }

  root.replaceChildren();
  root.setAttribute("role", "toolbar");
  root.setAttribute("aria-label", toolbarLabel);
  const currentItem = appDocument.createElement("div");
  currentItem.className = "rh-tools-current";
  currentItem.setAttribute("aria-label", "Current " + (config.currentNoun || "tool") + ": " + current.name);
  const currentLabel = appDocument.createElement("span");
  currentLabel.textContent = current.name;
  currentItem.append(icon(current.icon), currentLabel);
  root.appendChild(currentItem);
  const divider = appDocument.createElement("span");
  divider.className = "rh-tools-divider";
  divider.setAttribute("aria-hidden", "true");
  root.appendChild(divider);
  const triggers = appDocument.createElement("div");
  triggers.className = "rh-tools-triggers";
  config.groups.forEach(group => {
    const trigger = button(group.label, "rh-tools-trigger");
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.appendChild(icon("keyboard_arrow_down"));
    trigger.addEventListener("click", () =>
      menuAnchor === trigger ? closeMenu(true) : openMenu(trigger, group.label),
      { signal });
    trigger.addEventListener("keydown", event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openMenu(trigger, group.label, event.key === "ArrowUp");
      }
    }, { signal });
    triggers.appendChild(trigger);
  });
  root.appendChild(triggers);
  const mobileTrigger = button(config.mobileLabel || "Tools", "rh-tools-mobile-trigger");
  mobileTrigger.setAttribute("aria-haspopup", "menu");
  mobileTrigger.setAttribute("aria-expanded", "false");
  mobileTrigger.appendChild(icon("keyboard_arrow_down"));
  mobileTrigger.addEventListener("click", () =>
    menuAnchor === mobileTrigger ? closeMenu(true) : openMenu(mobileTrigger, "All " + itemNoun),
    { signal });
  mobileTrigger.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(mobileTrigger, "All " + itemNoun, event.key === "ArrowUp");
    }
  }, { signal });
  root.appendChild(mobileTrigger);
  const spacer = appDocument.createElement("span");
  spacer.className = "rh-tools-spacer";
  root.appendChild(spacer);
  const searchTrigger = button("", "rh-tools-search-trigger");
  searchTrigger.setAttribute("aria-label", "Search " + itemNoun + ", Control or Command K");
  searchTrigger.appendChild(icon("search"));
  const searchLabel = appDocument.createElement("span");
  searchLabel.className = "rh-tools-search-label";
  searchLabel.textContent = "Search " + itemNoun;
  searchTrigger.appendChild(searchLabel);
  const hint = appDocument.createElement("kbd");
  hint.textContent = "Ctrl+K";
  searchTrigger.appendChild(hint);
  searchTrigger.addEventListener("click", openSearch, { signal });
  root.appendChild(searchTrigger);

  const menu = appDocument.createElement("div");
  menu.className = "rh-tools-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  appDocument.body.appendChild(menu);
  const overlay = appDocument.createElement("div");
  overlay.className = "rh-tools-search-overlay";
  overlay.hidden = true;
  overlay.innerHTML =
    '<div class="rh-tools-search-dialog" role="dialog" aria-modal="true" aria-label="Search ' + itemNoun + '">' +
    '<div class="rh-tools-search-input-wrap">' +
    '<span class="material-symbols-rounded rh-tools-icon" aria-hidden="true">search</span>' +
    '<input type="search" role="combobox" aria-label="Search ' + itemNoun + '" aria-autocomplete="list" ' +
    'aria-expanded="true" aria-controls="rh-tools-search-results" placeholder="Search ' + itemNoun + '…">' +
    '<kbd>Esc</kbd></div>' +
    '<div id="rh-tools-search-results" class="rh-tools-search-results" role="listbox"></div></div>';
  appDocument.body.appendChild(overlay);
  const input = overlay.querySelector("input");
  const results = overlay.querySelector(".rh-tools-search-results");

  function placeMenu() {
    if (!menuAnchor || menu.hidden) return;
    const rect = menuAnchor.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.left, appWindow.innerWidth - menu.offsetWidth - 8)) + "px";
    const below = rect.bottom + 5;
    menu.style.top = (below + menu.offsetHeight <= appWindow.innerHeight - 8
      ? below : Math.max(8, rect.top - menu.offsetHeight - 5)) + "px";
  }
  function closeMenu(focus) {
    if (!menuAnchor) return;
    const anchor = menuAnchor;
    menuAnchor = null;
    anchor.setAttribute("aria-expanded", "false");
    menu.hidden = true;
    menu.replaceChildren();
    menuItems = [];
    if (focus) anchor.focus();
  }
  function addMenuItem(tool) {
    const item = button("", "rh-tools-menu-item");
    item.setAttribute("role", "menuitem");
    if (tool.name === current.name) item.setAttribute("aria-current", "page");
    item.append(icon(tool.icon), appDocument.createTextNode(tool.name));
    if (tool.name === current.name) item.appendChild(icon("check"));
    item.addEventListener("click", () => navigate(tool.name, true), { signal });
    menu.appendChild(item);
    menuItems.push(item);
  }
  function openMenu(anchor, groupName, last) {
    closeSearch(false);
    closeMenu(false);
    menuAnchor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    menu.setAttribute("aria-label", groupName);
    if (groupName === "All " + itemNoun) {
      config.groups.forEach(group => {
        const heading = appDocument.createElement("div");
        heading.className = "rh-tools-menu-heading";
        heading.textContent = group.label;
        heading.setAttribute("role", "presentation");
        menu.appendChild(heading);
        group.tools.forEach(addMenuItem);
      });
    } else {
      config.groups.find(group => group.label === groupName).tools.forEach(addMenuItem);
    }
    menu.hidden = false;
    placeMenu();
    menuItems[last ? menuItems.length - 1 : 0].focus();
  }
  menu.addEventListener("keydown", event => {
    const index = menuItems.indexOf(appDocument.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      menuItems[(index + (event.key === "ArrowDown" ? 1 : -1) + menuItems.length)
        % menuItems.length].focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      menuItems[event.key === "Home" ? 0 : menuItems.length - 1].focus();
    } else if (event.key === "Tab") {
      closeMenu(false);
    }
  }, { signal });
  function setActiveResult() {
    resultItems.forEach((item, index) =>
      item.setAttribute("aria-selected", index === activeResult ? "true" : "false"));
    if (resultItems[activeResult]) {
      input.setAttribute("aria-activedescendant", resultItems[activeResult].id);
      resultItems[activeResult].scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }
  function renderResults() {
    const query = input.value.trim().toLowerCase();
    const matched = tools.map((tool, index) => {
      const name = tool.name.toLowerCase();
      const group = tool.group.toLowerCase();
      const description = tool.description.toLowerCase();
      const score = !query || name.startsWith(query) ? 0
        : name.includes(query) ? 1
        : group.includes(query) ? 2
        : description.includes(query) ? 3 : 4;
      return { tool, index, score };
    }).filter(item => item.score < 4)
      .sort((a, b) => a.score - b.score || a.index - b.index)
      .map(item => item.tool);
    activeResult = Math.min(activeResult, Math.max(0, matched.length - 1));
    results.replaceChildren();
    resultItems = [];
    if (!matched.length) {
      const empty = appDocument.createElement("p");
      empty.className = "rh-tools-search-empty";
      empty.textContent = "No matching " + itemNoun;
      results.appendChild(empty);
    }
    matched.forEach((tool, index) => {
      const item = button("", "rh-tools-search-result");
      item.id = "rh-tools-search-result-" + index;
      item.setAttribute("role", "option");
      item.appendChild(icon(tool.icon));
      const name = appDocument.createElement("span");
      name.textContent = tool.name;
      const group = appDocument.createElement("span");
      group.className = "rh-tools-search-result-group";
      group.textContent = tool.group;
      item.append(name, group);
      item.addEventListener("mouseenter", () => {
        activeResult = index;
        setActiveResult();
      }, { signal });
      item.addEventListener("click", () => navigate(tool.name, true), { signal });
      results.appendChild(item);
      resultItems.push(item);
    });
    setActiveResult();
  }
  function openSearch() {
    closeMenu(false);
    if (!overlay.hidden) return input.focus();
    returnFocus = appDocument.activeElement;
    overlay.hidden = false;
    input.value = "";
    activeResult = 0;
    renderResults();
    input.focus();
  }
  function closeSearch(focus) {
    if (overlay.hidden) return;
    overlay.hidden = true;
    if (focus && returnFocus && returnFocus.isConnected) returnFocus.focus();
    returnFocus = null;
  }
  input.addEventListener("input", () => {
    activeResult = 0;
    renderResults();
  }, { signal });
  overlay.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeSearch(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!resultItems.length) return;
      activeResult = (activeResult + (event.key === "ArrowDown" ? 1 : -1)
        + resultItems.length) % resultItems.length;
      setActiveResult();
    } else if (event.key === "Enter" && resultItems[activeResult]) {
      event.preventDefault();
      resultItems[activeResult].click();
    } else if (event.key === "Tab") {
      const focusable = [input].concat(resultItems);
      const index = focusable.indexOf(appDocument.activeElement);
      if (event.shiftKey && index === 0) {
        event.preventDefault();
        focusable[focusable.length - 1].focus();
      } else if (!event.shiftKey && index === focusable.length - 1) {
        event.preventDefault();
        input.focus();
      }
    }
  }, { signal });
  overlay.addEventListener("pointerdown", event => {
    if (event.target === overlay) closeSearch(true);
  }, { signal });
  appDocument.addEventListener("pointerdown", event => {
    if (menuAnchor && !root.contains(event.target) && !menu.contains(event.target))
      closeMenu(false);
  }, { signal });
  appDocument.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openSearch();
    } else if (event.key === "Escape" && menuAnchor) {
      event.preventDefault();
      closeMenu(true);
    }
  }, { signal });
  appWindow.addEventListener("resize", placeMenu, { signal });
  appWindow.addEventListener("scroll", placeMenu, { signal, capture: true });
  appWindow.addEventListener("popstate", () => {
    if (!queryParam) return;
    const name = new URL(appWindow.location.href).searchParams.get(queryParam);
    if (name && byName.has(name) && name !== current.name) navigate(name, false);
  }, { signal });
  updateUrl(current.name, true);
  const observer = new MutationObserver(() => {
    if (!root.isConnected) destroy();
  });
  observer.observe(appDocument.body, { childList: true, subtree: true });
  function destroy() {
    controller.abort();
    observer.disconnect();
    menu.remove();
    overlay.remove();
    if (appWindow[controllerSlot] &&
        appWindow[controllerSlot].destroy === destroy) appWindow[controllerSlot] = null;
  }
  appWindow[controllerSlot] = { destroy };
};
