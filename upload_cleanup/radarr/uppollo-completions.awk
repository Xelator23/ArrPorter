# Parse the supplied upPollo text log format, never execute its contents.
# Output: start ISO timestamp TAB finish ISO timestamp TAB full source path.
# Overlapping uploads are never accepted: terminal messages have no job ID.
# Resume only after every observed start has a terminal success/error message.
function timestamp(line, t) {
    t=line; sub(/^time=/,"",t); sub(/ .*/,"",t)
    return t
}
/^time=.* msg="New Upload: / {
    if (active) poisoned=1
    active++
    start=timestamp($0)
    path=$0; sub(/^.* msg="New Upload: /,"",path)
    if (path !~ /"$/) { poisoned=1; next }
    sub(/"$/,"",path)
    # Reject escaped paths rather than guessing how the logger encodes them.
    if (path ~ /[\\"\t\r]/ || path !~ /^\//) poisoned=1
    next
}
/^time=.* msg="Upload processed successfully"$/ {
    if (active == 1 && !poisoned) print start "\t" timestamp($0) "\t" path
    if (active > 0) active--
    if (!active) poisoned=0
    next
}
/^time=.* msg="Error during upload processing:/ {
    if (active > 0) active--
    if (!active) poisoned=0
    next
}
