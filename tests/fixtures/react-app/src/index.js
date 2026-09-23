const React = require("react");
const { renderToString } = require("react-dom/server");

const html = renderToString(React.createElement("h1", null, "Hello from an offline kit"));
console.log(html);
