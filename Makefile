# kilix-embed-pane. `make test` builds the library and runs every test.
# Each layer adds its own objects and tests below; see PLAN.md for owners.

CC ?= cc
CFLAGS ?= -O2 -g
override CFLAGS += -std=c11 -Wall -Wextra -Wpedantic -Werror -fPIC -Iinclude
BUILD ?= build
PYTHON ?= python3

# Source layer
SOURCE_OBJS = $(BUILD)/source_map.o $(BUILD)/source_rc.o
SOURCE_TESTS = $(BUILD)/test_source

OBJS = $(SOURCE_OBJS)
C_TESTS = $(SOURCE_TESTS)

all: $(BUILD)/libkilix-embed-pane.a $(BUILD)/libkilix-embed-pane.so

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: src/%.c include/*.h | $(BUILD)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/libkilix-embed-pane.a: $(OBJS)
	$(AR) rcs $@ $^

$(BUILD)/libkilix-embed-pane.so: $(OBJS)
	$(CC) -shared -o $@ $^

$(BUILD)/test_%: tests/%.c $(BUILD)/libkilix-embed-pane.a
	$(CC) $(CFLAGS) -o $@ $< $(BUILD)/libkilix-embed-pane.a $(LDLIBS)

test: $(C_TESTS)
	@for t in $(C_TESTS); do echo "== $$t"; ./$$t || exit 1; done
	$(PYTHON) -I -m unittest discover -s tests -p 'test_python.py'

clean:
	rm -rf $(BUILD)

.PHONY: all test clean

$(BUILD)/test_source: LDLIBS += -lpthread
