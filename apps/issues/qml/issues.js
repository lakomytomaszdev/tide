.pragma library

function stateLabel(state) {
    return ({draft: "Szkic", ready: "Gotowa do przyjęcia", accepted: "Przyjęta",
             rejected: "Odrzucona", archived: "Archiwalna"})[state] || state
}

function rows(issues, query, project, state) {
    const needle = query.toLocaleLowerCase().trim()
    return (issues || []).filter(i => (!project || i.project === project)
        && (state === "all" || (state === "open" ? ["draft", "ready"].indexOf(i.state) >= 0 : i.state === state))
        && (!needle || [i.id, i.title, i.project || ""].join(" ").toLocaleLowerCase().indexOf(needle) >= 0))
}

function projectOptions(projects, first) {
    return [{label: first, value: ""}].concat((projects || []).map(p => ({label: p.name || p.slug, value: p.slug})))
}
