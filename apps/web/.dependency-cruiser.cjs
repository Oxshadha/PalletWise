/** Feature boundaries (engineering baseline 4.2): features talk only through shared modules. */
module.exports = {
  forbidden: [
    {
      name: "features-do-not-import-each-other",
      comment:
        "A feature must not import another feature's internals; use shared stores and the API client.",
      severity: "error",
      from: { path: "^src/features/([^/]+)/" },
      to: { path: "^src/features/([^/]+)/", pathNot: "^src/features/$1/" },
    },
    {
      name: "shared-does-not-import-features",
      severity: "error",
      from: { path: "^src/shared/" },
      to: { path: "^src/features/" },
    },
    {
      name: "no-circular",
      severity: "error",
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsConfig: { fileName: "tsconfig.json" },
  },
};
