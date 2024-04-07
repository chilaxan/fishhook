#include <Python.h>

#if !defined(_WIN32)
    #include <unistd.h>
    #include <stdio.h>
    #include <stdlib.h>
    #include <errno.h>
    #include <sys/mman.h>
#else
    #include <Windows.h>
    #include <Memoryapi.h>
#endif

#if !defined(_WIN32)
    int PREAD = PROT_READ;
    int PWRITE = PROT_WRITE;
    int PEXEC = PROT_EXEC;
#else
    int PREAD = 1 << 1;
    int PWRITE = 1 << 2;
    int PEXEC = 1 << 3;
#endif

void changeProts(Py_buffer buffer, int prots) {
    unsigned long long address = (unsigned long long)buffer.buf;
    size_t length = buffer.len;
    #if !defined(_WIN32)
        int pagesize = sysconf(_SC_PAGE_SIZE);
        unsigned long long addr_align = address & ~(pagesize - 1);
        unsigned long long mem_end = (address + length) & ~(pagesize - 1);
        if ((address + length) > mem_end) {
            mem_end += pagesize;
        }
        size_t memlen = mem_end - addr_align;
        mprotect((void *)addr_align, memlen, prots);
    #else
        int old;
        int flags;
        if (prots & PREAD && prots & PWRITE && prots & PEXEC) {
            flags = PAGE_EXECUTE_READWRITE;
        } else if (prots & PREAD && prots & PWRITE) {
            flags = PAGE_READWRITE;
        } else if (prots & PREAD && prots & PEXEC) {
            flags = PAGE_EXECUTE_READ;
        } else {
            flags = PAGE_READONLY;
        }
        VirtualProtect((LPVOID)address, length, flags, &old);
    #endif
}

void invalidateInstructionCache(void *addr, size_t length) {
    #if defined(_WIN32)
        FlushInstructionCache(GetCurrentProcess(), (unsigned char*)addr, length);
    #elif defined(__has_builtin)
        #if __has_builtin(__builtin___clear_cache)
            __builtin___clear_cache((char*)addr, (char*)(addr + length));
        #endif
    #else
       // cannot determine way to clear instruction cache
       // things might work, and might not
       // If you get weird crashes, make an issue: https://github.com/chilaxan/fishhook
    #endif
}

static PyObject *method_writeExecutableMemory(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject* target = NULL;
    PyObject* src = NULL;
    int prot_after = PREAD | PEXEC;

    Py_buffer target_buf, src_buf;

    static char *kwlist[] = { "target", "src", "prot_after", NULL };
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|i", kwlist, &target, &src, &prot_after)) {
        return NULL;
    }

    if (
        PyObject_GetBuffer(target, &target_buf, PyBUF_FULL_RO) == -1
        || PyObject_GetBuffer(src, &src_buf, PyBUF_FULL_RO) == -1
        ) {
        return NULL;
    }

    if (target_buf.len != src_buf.len) {
        PyErr_SetString(PyExc_ValueError, "target and src must be the same length");
        return NULL;
    }

    changeProts(target_buf, PREAD | PWRITE);
    memcpy(target_buf.buf, src_buf.buf, target_buf.len);
    changeProts(target_buf, prot_after);
    invalidateInstructionCache(target_buf.buf, target_buf.len);

    PyBuffer_Release(&target_buf);
    PyBuffer_Release(&src_buf);
    Py_RETURN_NONE;
}

static PyMethodDef AsmMethods[] = {
    {"writeExecutableMemory", (PyCFunction) method_writeExecutableMemory, METH_VARARGS | METH_KEYWORDS, "write src into target executable memory"},
    {NULL, NULL, 0, NULL}
};


static struct PyModuleDef asmmodule = {
    PyModuleDef_HEAD_INIT,
    "fishhook._asm",
    "provides writeExecutableMemory function",
    -1,
    AsmMethods
};

PyMODINIT_FUNC PyInit__asm(void) {
    PyObject* module = PyModule_Create(&asmmodule);
    PyModule_AddIntConstant(module, "PREAD", PREAD);
    PyModule_AddIntConstant(module, "PWRITE", PWRITE);
    PyModule_AddIntConstant(module, "PEXEC", PEXEC);
    return module;
}