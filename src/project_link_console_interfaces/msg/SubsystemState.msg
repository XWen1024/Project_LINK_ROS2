uint8 SEVERITY_OK=0
uint8 SEVERITY_WARN=1
uint8 SEVERITY_ERROR=2
uint8 SEVERITY_STALE=3

string name
string display_name
uint8 severity
string active_state
string sub_state
string result
bool ready
bool stale
string message
builtin_interfaces/Time since
uint32 restart_count
diagnostic_msgs/KeyValue[] metrics
