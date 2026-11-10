"""Apollo documents are built from introspection, in the shape used by the iOS queries."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src/viewlift_ai/web/static/js/graphql.js"

NODE = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

global.window = global;
global.document = { addEventListener() {} };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const doc = global.VLGraphQLDoc;

const stringType = { kind: "SCALAR", name: "String" };
const idType = { kind: "SCALAR", name: "ID" };
const schema = {
  queryType: { name: "Query" },
  types: [
    {
      name: "Query",
      kind: "OBJECT",
      fields: [
        {
          name: "identityUserProfile",
          args: [
            { name: "site", type: { kind: "NON_NULL", ofType: stringType } },
            { name: "language", type: stringType },
          ],
          type: { kind: "OBJECT", name: "Profile" },
        },
        {
          name: "contentList",
          args: [
            {
              name: "ids",
              type: { kind: "LIST", ofType: { kind: "NON_NULL", ofType: stringType } },
            },
          ],
          type: {
            kind: "NON_NULL",
            ofType: { kind: "LIST", ofType: { kind: "NON_NULL", ofType: { kind: "INTERFACE", name: "Content" } } },
          },
        },
        {
          name: "node",
          args: [],
          type: { kind: "OBJECT", name: "Node" },
        },
      ],
    },
    {
      name: "Profile",
      kind: "OBJECT",
      fields: [
        { name: "name", type: stringType },
        { name: "parent", type: { kind: "OBJECT", name: "Profile" } },
        { name: "__typename", type: stringType },
      ],
    },
    {
      name: "Content",
      kind: "INTERFACE",
      fields: [{ name: "id", type: idType }],
      possibleTypes: [{ kind: "OBJECT", name: "Video" }],
    },
    {
      name: "Video",
      kind: "OBJECT",
      fields: [
        { name: "id", type: idType },
        { name: "title", type: stringType },
      ],
    },
    {
      name: "Node",
      kind: "OBJECT",
      fields: [
        { name: "id", type: idType },
        { name: "child", type: { kind: "OBJECT", name: "Node" } },
      ],
    },
    {
      name: "Device",
      kind: "ENUM",
      enumValues: [{ name: "IOS" }, { name: "APPLETV" }],
    },
  ],
};

const profile = doc.operation(schema, "query", schema.types[0].fields[0]);
assert.equal(profile, `query IdentityUserProfile($site: String!, $language: String) {
  identityUserProfile(
    site: $site
    language: $language
  ) {
    name
  }
}
`);
assert.doesNotMatch(profile, /__typename/);
assert.doesNotMatch(profile, /\.\.\.(?! on\b)/);

const list = doc.operation(schema, "query", schema.types[0].fields[1]);
assert.match(list, /\$ids: \[String!\]/);
assert.match(list, /\.\.\. on Video \{/);
assert.match(list, /title/);
assert.doesNotMatch(list, /\.\.\.(?! on\b)/);

const node = doc.operation(schema, "query", schema.types[0].fields[2]);
assert.match(node, /id/);
assert.doesNotMatch(node, /child/);

assert.equal(doc.selection(schema, "Device"), "IOS\nAPPLETV\n");
"""


def test_graphql_document_matches_ios_query_shape() -> None:
    node = shutil.which("node")
    assert node, "node is required"
    subprocess.run([node, "--check", str(SCRIPT)], check=True)
    subprocess.run([node, "-e", NODE, str(SCRIPT)], check=True)
